"""sim/chaos_web: Synthetic test internet and ground truth generator for WARDEN.

Provides simulated web pages across three distinct archetypes:
1. /pages/price/{page_id}: Static product page with signal (price changes) and noise.
2. /pages/jobs/{page_id}: Static job board with signal (add/remove) and noise (relative dates).
3. /pages/app/{page_id}: Single-page application requiring JavaScript execution to render.

GROUND TRUTH NOTE:
The `/__truth__/{page_id}` endpoint returns an authoritative log of all mutations applied to that
page, tagged as either `signal` (meaningful change) or `noise` (cosmetic change). This endpoint
exists solely as an evaluation oracle and would never exist on a real-world web target.
"""

import asyncio
import random
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse


@dataclass
class MutationRecord:
    at: str
    kind: str  # "signal" | "noise"
    description: str


@dataclass
class PageState:
    page_type: str  # "price" | "jobs" | "app"
    page_id: str
    version: int = 1
    # Price page fields
    price: float = 99.99
    last_updated: float = field(default_factory=time.time)
    banner_id: int = 1
    # Jobs page fields
    jobs: list[dict[str, str]] = field(default_factory=list)
    # SPA app fields
    app_data: str = "Initial Dashboard State"
    # Ground truth history
    mutations: list[MutationRecord] = field(default_factory=list)


# In-memory storage for simulated pages
_PAGES: dict[str, PageState] = {}
_RUNNING_TASKS: list[asyncio.Task[None]] = []


def _get_or_create_page(page_type: str, page_id: str) -> PageState:
    key = f"{page_type}:{page_id}"
    if key not in _PAGES:
        state = PageState(page_type=page_type, page_id=page_id)
        if page_type == "jobs":
            state.jobs = [
                {"id": "j1", "title": "Staff Backend Engineer", "posted_days": "2"},
                {"id": "j2", "title": "Systems Architect", "posted_days": "5"},
            ]
        _PAGES[key] = state
    return _PAGES[key]


def _apply_mutation(state: PageState) -> MutationRecord:
    """Apply a random mutation to the page state, tagging it as signal or noise."""
    now_iso = datetime.now(UTC).isoformat()
    state.version += 1

    if state.page_type == "price":
        # 30% chance of signal (price change), 70% chance of noise (banner/timestamp)
        if random.random() < 0.3:
            old_price = state.price
            state.price = round(old_price * random.choice([0.9, 0.95, 1.05, 1.1]), 2)
            rec = MutationRecord(
                at=now_iso,
                kind="signal",
                description=f"Price changed from ${old_price} to ${state.price}",
            )
        else:
            state.last_updated = time.time()
            state.banner_id = (state.banner_id % 5) + 1
            rec = MutationRecord(
                at=now_iso,
                kind="noise",
                description=f"Timestamp tick and banner rotated to banner_{state.banner_id}.jpg",
            )

    elif state.page_type == "jobs":
        # 40% chance of signal (add/remove job), 60% chance of noise (posted X days ago increment)
        if random.random() < 0.4:
            if len(state.jobs) > 1 and random.random() < 0.5:
                removed = state.jobs.pop()
                rec = MutationRecord(
                    at=now_iso,
                    kind="signal",
                    description=f"Removed job posting: {removed['title']}",
                )
            else:
                new_id = f"j{len(state.jobs) + 1}"
                new_job = {
                    "id": new_id,
                    "title": f"Distributed Systems Specialist #{new_id}",
                    "posted_days": "1",
                }
                state.jobs.append(new_job)
                rec = MutationRecord(
                    at=now_iso,
                    kind="signal",
                    description=f"Added new job posting: {new_job['title']}",
                )
        else:
            for j in state.jobs:
                current_days = int(j.get("posted_days", "1"))
                j["posted_days"] = str(current_days + 1)
            rec = MutationRecord(
                at=now_iso,
                kind="noise",
                description="Incremented relative posting timestamps",
            )

    else:  # SPA app page
        if random.random() < 0.4:
            rev = random.randint(100, 500)
            state.app_data = f"Updated Metrics Dashboard (Quarterly Revenue: ${rev}k)"
            rec = MutationRecord(
                at=now_iso,
                kind="signal",
                description="Dashboard metrics payload updated",
            )
        else:
            rec = MutationRecord(
                at=now_iso,
                kind="noise",
                description="Session tracking ping and token refresh",
            )

    state.mutations.append(rec)
    return rec


async def _poisson_mutator(interval_s: float = 10.0) -> None:
    """Background task applying Poisson-process mutations across active simulated pages."""
    while True:
        # Exponential inter-arrival time generates a Poisson process
        delay = random.expovariate(1.0 / interval_s)
        await asyncio.sleep(delay)
        for state in list(_PAGES.values()):
            _apply_mutation(state)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage background mutation generation tasks."""
    task = asyncio.create_task(_poisson_mutator(interval_s=15.0))
    _RUNNING_TASKS.append(task)
    yield
    for t in _RUNNING_TASKS:
        t.cancel()


app = FastAPI(title="chaos-web", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/robots.txt", response_class=HTMLResponse)
async def robots_txt() -> str:
    return "User-agent: *\nAllow: /\n"


@app.get("/pages/price/{page_id}", response_class=HTMLResponse)
async def get_price_page(page_id: str) -> str:
    """Archetype 1: Static price page.

    Tier-1 sufficient (no JavaScript required to parse price).
    """
    state = _get_or_create_page("price", page_id)
    return f"""<!DOCTYPE html>
<html>
<head><title>Product {page_id} Details</title></head>
<body>
    <div id="promotions">
        <img src="/static/banner_{state.banner_id}.jpg" alt="Promo Banner" />
    </div>
    <h1>Product Item {page_id}</h1>
    <div class="product-info">
        <span class="price-tag">${state.price:.2f}</span>
        <span class="availability">In Stock</span>
    </div>
    <footer>
        <span data-timestamp="{state.last_updated}">Checked: {state.last_updated}</span>
    </footer>
</body>
</html>"""


@app.get("/pages/jobs/{page_id}", response_class=HTMLResponse)
async def get_jobs_page(page_id: str) -> str:
    """Archetype 2: Static job board.

    Tier-1 sufficient.
    """
    state = _get_or_create_page("jobs", page_id)
    jobs_html = "".join(
        f'<li id="{j["id"]}"><h3>{j["title"]}</h3><p>Posted {j["posted_days"]} days ago</p></li>'
        for j in state.jobs
    )
    return f"""<!DOCTYPE html>
<html>
<head><title>Careers at Organization {page_id}</title></head>
<body>
    <h1>Open Opportunities</h1>
    <ul class="job-list">
        {jobs_html}
    </ul>
    <div class="footer-notice">Copyright 2026 Organization {page_id}</div>
</body>
</html>"""


@app.get("/pages/app/{page_id}", response_class=HTMLResponse)
async def get_spa_page(page_id: str) -> str:
    """Archetype 3: Single-page application (SPA).

    HTML is nearly empty on arrival; real content is injected client-side via JavaScript.
    Invisible to Tier-1 fetches; requires Tier-2 Selenium rendering.
    """
    state = _get_or_create_page("app", page_id)
    inner = (
        f'<div class="spa-content">'
        f"<h2>Active Enterprise Dashboard</h2>"
        f'<p class="data-view">{state.app_data}</p>'
        f"</div>"
    )
    return f"""<!DOCTYPE html>
<html>
<head>
    <title>Enterprise Portal {page_id}</title>
</head>
<body>
    <div id="root">
        <div class="loading-spinner">Loading application state...</div>
    </div>
    <script>
        setTimeout(function() {{
            var root = document.getElementById("root");
            root.innerHTML = '{inner}';
        }}, 100);
    </script>
</body>
</html>"""


@app.get("/__truth__/{page_id}")
async def get_ground_truth(page_id: str) -> list[dict[str, str]]:
    """Return authoritative ground-truth mutation log for the requested page.

    TEST HARNESS ONLY:
    This endpoint would never exist on a real target website. It exists solely so our
    Gate 1 evaluation script can evaluate change classifier accuracy against ground truth.
    """
    for _key, state in _PAGES.items():
        if state.page_id == page_id:
            return [
                {"at": m.at, "kind": m.kind, "description": m.description} for m in state.mutations
            ]
    raise HTTPException(status_code=404, detail="Page ID not found in simulator history")


@app.post("/__reset__/{page_id}")
async def reset_page(page_id: str) -> dict[str, str]:
    """Reset a page to its initial state and clear its ground-truth mutation log."""
    for key in list(_PAGES.keys()):
        if _PAGES[key].page_id == page_id:
            del _PAGES[key]
    return {"status": "reset_successful", "page_id": page_id}


@app.post("/__mutate__/{page_type}/{page_id}")
async def trigger_manual_mutation(page_type: str, page_id: str) -> dict[str, str]:
    """Test helper to trigger an immediate, deterministic mutation on demand."""
    state = _get_or_create_page(page_type, page_id)
    rec = _apply_mutation(state)
    return {"status": "mutated", "kind": rec.kind, "description": rec.description}
