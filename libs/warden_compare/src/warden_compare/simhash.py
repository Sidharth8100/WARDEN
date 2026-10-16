"""SimHash near-duplicate fingerprinting stub and Hamming distance."""


def compute_simhash(text: str, *, hash_bits: int = 64, shingle_size: int = 4) -> int:
    """[YOU IMPLEMENT] Compute a SimHash fingerprint of `text` as an
    unsigned integer with `hash_bits` bits, for near-duplicate
    detection: similar documents should produce fingerprints with a
    SMALL Hamming distance (few differing bits), dissimilar documents
    a LARGE one — unlike a normal hash, where a one-character change
    produces a completely unrelated hash.

    Do not use any existing simhash/minhash/datasketch library — the
    whole point is implementing the bit-voting scheme yourself.

    Steps (standard SimHash construction):
    1. Break `text` into overlapping character or word shingles of
       size `shingle_size`.
    2. Weight each shingle (plain frequency count is a reasonable
       weight; justify your choice).
    3. Hash EACH SHINGLE INDIVIDUALLY to a `hash_bits`-bit value (any
       stable hash works here — this is not the clever part).
    4. Maintain a `hash_bits`-length vector of weighted votes: for
       each shingle, for each bit position, add the shingle's weight
       to that position's vote if the shingle's hash has a 1 there,
       subtract it if the shingle's hash has a 0 there.
    5. The final fingerprint's bit i is 1 if vote[i] > 0, else 0.

    Hint L1: step 4-5 is "weighted majority vote per bit position
    across all shingles" — this is the entire trick; everything else
    is bookkeeping.
    Hint L2: use Python's built-in hashlib (e.g. blake2b with a
    digest_size matching hash_bits//8) per shingle, convert the
    digest to an integer, and check each bit with `(h >> i) & 1`.
    Hint L3: accumulate votes in a list of floats of length
    hash_bits, initialized to 0; after processing all shingles,
    build the final int bit by bit from the sign of each vote.

    Must satisfy: compute_simhash("", ...) returns a well-defined
    value (document and test what it is, e.g. 0); identical text
    produces identical fingerprints; the function is otherwise
    deterministic (same input, same output, always).
    """
    raise NotImplementedError("see docstring")


def hamming_distance(a: int, b: int) -> int:
    """Count differing bits between two fingerprints of the same width.

    (AI-implemented — a one-liner using bin(a ^ b).count('1')
    or int.bit_count() on Python 3.10+; not the hard part.)
    """
    return (a ^ b).bit_count()
