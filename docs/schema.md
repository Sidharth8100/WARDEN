Queries this system must answer
auth -> The user enters his registered email and I give him referesh token by some hashing shit.
API -> User's list of monitors and their results + statistics about the monitors. For the user's home page.

API -> Check whether the monitor is already existent in the list of monitors of the given user.

Scheduler -> Parsing kab konsi site ki ki jayegi

Processor -> Most recenbt snapshot for a monitor + K last snapshots to get analytics.



Schemas ->

users:

id -> UUID ,  primary key (auto implies the field is non null)
email -> citext (case insensitive text), not null, unique
password_hash -> not null, text
role -> text, not null, check(role in ('user', 'admin')), default 'user'
plan -> text , not null, foreign key for the plans table, names of different plans
created_at -> timestamptz, default now()

plans:

code : Text, default 'free', -- values like 'free' , 'plus', 'pro'
maximum_monitors: int not null
frequency: int not null --min_interval_in_seconds

refresh_families:
id uuid primary key
user_id foreign key for users(id)
revoked_at timestamptz
revoked_reason text

refresh_tokens:
id uuid primary key
family_id uuid not null references refresh_families(id)
token_hash not null bytea unique
parent_id uuid references(refresh_tokens(id))
used_at timestamptz not null
expires_at timestamptz not null


monitors:
id              uuid primary key
user_id         uuid not null references users(id)
url             text not null
url_canonical   text not null
url_hash        bytea not null
domain          text not null
fetch_tier      smallint not null default 1
needs_render    boolean not null default false
selector_config jsonb
rule_src        text
min_interval_s  int not null
max_interval_s  int not null
next_due_at     timestamptz not null
state           text not null default 'active'   -- CHECK (state in ('active','paused','deleted'))
version         int not null default 1            -- for optimistic concurrency, Day 3
created_at      timestamptz not null default now()
updated_at      timestamptz not null default now()
unique (user_id, url_hash)
index on (domain)                                        -- for shard routing, Day 11
partial index on (next_due_at) where state = 'active'     -- for query 4


snapshots:
id              bigint generated always as identity
monitor_id      uuid not null references monitors(id)
fetched_at      timestamptz not null
kind            text not null   -- 'keyframe' | 'delta'
base_snapshot_id bigint references snapshots(id)
content_hash    bytea not null
simhash         bigint
blob_ref        text
screenshot_ref  text
http_status     int
etag            text
size_bytes      int
tier            smallint

primary key (id, fetched_at)   -- note: this shape, see below
index on (monitor_id, fetched_at desc)   -- for query 5


outbox:
id              bigint generated always as identity primary key
kind            text not null
payload         jsonb not null
idempotency_key text unique not null
available_at    timestamptz not null default now()
attempts        int not null default 0
status          text not null default 'pending'   -- CHECK (status in ('pending','sent','failed'))
created_at      timestamptz not null default now()

partial index on (available_at) where status = 'pending'   -- for query 7


feedback_labels:
event_id          bigint not null references change_events(id)
user_id           uuid not null references users(id)
label             text not null   -- 'useful' | 'noise'
feature_snapshot  jsonb
created_at        timestamptz not null default now()

unique (event_id, user_id)
