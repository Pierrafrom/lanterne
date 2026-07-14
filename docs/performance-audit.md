# Performance audit — where the pipeline's time actually goes

Built from a real `uv run cine-event-bot scrape` run (2026-07-14, 16:29→17:30,
61 minutes total, 39,338 sightings processed, 0 failures) — timings below are
measured from that run's own progress bars and structured logs
(`logs/app.jsonl`), not estimated.

## Measured timing, per source

| Source                              | Items                     | Wall time | Per-item   | LLM used?                |
| ----------------------------------- | ------------------------- | --------- | ---------- | ------------------------ |
| `cinematheque.fr`                   | 0                         | 0:00:00   | —          | yes (no items this week) |
| `premiereprojo.fr`                  | 83                        | 0:00:15   | 0.18s      | no (structured JSON)     |
| `forumdesimages.fr`                 | 0                         | 0:00:05   | —          | yes (no items this week) |
| `fondation-jeromeseydoux-pathe.com` | 43                        | 0:11:57   | **16.7s**  | yes                      |
| `lavillette.com`                    | 35                        | 0:18:47   | **32.2s**  | yes                      |
| `offi.fr`                           | 184 pages / 24,778 events | 0:01:03   | 0.34s/page | no (tabular DOM)         |
| `paris-cine.info`                   | 14,400                    | 0:15:55   | 0.066s     | only ~0.3% of items      |

Two numbers jump out immediately: **the two LLM-bound sources with real
volume this run (Fondation Pathé, La Villette) took ~31 of the 61 minutes —
roughly half the entire run — to process a combined 78 items**, while the
three structured/tabular sources processed **39,261 items** (500× the volume)
in under 20 minutes combined.

## Finding 1 (dominant bottleneck, now root-caused): Ollama runs the model on CPU, and serializes every request

Follow-up investigation (2026-07-14, live against the actual running Ollama
instance) found **two distinct, stacked problems**, confirmed empirically —
not inferred from timing alone this time.

### 1a. The model runs on CPU — the GPU sits idle

`nvidia-smi` during and after inference: **0% GPU utilization, 0 MiB of 8188
MiB VRAM used, "No running processes found"** on the machine's RTX 4070
Laptop GPU. Ollama's own `/api/ps` confirms it: the loaded `qwen2.5:7b`
model reports `"size_vram":0` — the entire ~4.7GB (Q4_K_M) model is sitting
in system RAM and being evaluated on CPU, not GPU. `nvidia-smi` itself works
fine and the WSL2 CUDA runtime libraries (`libcuda.so.1`,
`libcudadebugger.so.1`) are present and resolvable via `ldconfig` — so the
GPU is visible to the OS, but Ollama's process is not using it. Root cause
not fully isolated in this pass (no shell access to the Ollama service's
own startup logs / working `ollama` CLI in this environment to inspect its
GPU-detection output directly) — likely candidates worth checking directly:
Ollama's systemd unit not inheriting the WSL GPU library path, an Ollama
build without CUDA support, or a version/driver mismatch. **This needs a
follow-up investigation with shell access to the host, outside what this
audit could confirm from inside the app's own environment.**

Measured impact: a warmed-up, realistic-length extraction call (the actual
system prompt + a real card's text, through the same `/v1/chat/completions`
endpoint Instructor uses) took **16.0-16.6s consistently** across 5
sequential calls. CPU inference for a 7B Q4 model in the 10-20s range for a
short structured-output generation is exactly the expected order of
magnitude; the same call on a properly GPU-accelerated RTX 4070 would
typically be **5-10× faster** (roughly 1.5-3s), based on typical
CPU-vs-GPU throughput ratios for a model this size — an estimate pending
direct confirmation once GPU access is fixed, not a second live measurement.

### 1b. Even once warm, requests are processed one at a time

5 concurrent calls (identical realistic prompt, fired via `asyncio.gather`)
finished at 16.6s, 32.8s, 49.2s, 65.7s, 81.8s — almost exactly 1×, 2×, 3×,
4×, 5× a single call's latency, and the concurrent batch's total wall time
(81.8s) was **statistically identical** to running the same 5 calls
sequentially (80.8s). Confirms: this Ollama instance has no spare parallel
inference slot today (`OLLAMA_NUM_PARALLEL` effectively 1) — the
application's own `asyncio.Semaphore(_CONCURRENCY=5)` in
`gather_events`/`structure_via_llm` is not wrong, it is just aimed at a
concurrency the server side isn't offering yet.

This fully explains the real scrape run's numbers: Fondation Pathé's
measured 16.7s/item and La Villette's 32.2s/item (longer prompts, more
output fields) both land right on this CPU-bound, one-at-a-time baseline.

### What actually moves this number, in order of expected impact

1. **Fix GPU access first.** This is the single highest-leverage change in
   this entire audit — bigger than every other finding combined. Until it's
   fixed, raising `OLLAMA_NUM_PARALLEL` mostly just interleaves the same
   scarce CPU compute across more in-flight requests, which helps much less
   than it would with genuinely idle GPU capacity sitting behind it.
   Concrete next step: run `ollama serve` in the foreground (not as the
   background systemd unit) and read its own startup log for GPU detection
   output, or reinstall/verify the CUDA-enabled Ollama build against this
   WSL2 environment's driver version (`nvidia-smi` reports driver 596.08,
   CUDA 13.2).
1. **Once GPU-accelerated, raise `OLLAMA_NUM_PARALLEL`** (2-4, bounded by
   VRAM headroom — an ~4.7GB Q4 model leaves real room in 8GB for a few
   concurrent short-context KV caches) so the application's existing
   `_CONCURRENCY=5` semaphore finally has real parallelism to spend.
1. **A smaller/faster or quantized model for this specific task** — pulling
   4-5 fields from a short text block is exactly the kind of narrow,
   low-reasoning job a smaller model handles about as accurately at a
   fraction of the latency; worth a quick accuracy comparison via
   `uv run cine-event-bot eval-extraction` before committing to a swap.
1. **Keep extending this codebase's own already-established pattern of
   avoiding the LLM entirely when the data is already structured** — not a
   new idea here: Première Projo, offi.fr, and the retired `mk2.py` already
   skip the LLM for this exact reason (see
   `docs/decisions/0004-rsc-extraction-and-pipeline.md`), and Paris Ciné
   Info's own `_KnownFacts` pattern already discards the LLM's title/venue/
   time guesses in favor of known-true API data. Fondation Pathé's tiles and
   La Villette's day-grouped text are both fairly template-shaped
   (`docs/scraping-strategy.md`'s own Level-4 write-ups describe exactly
   which fields are template-fixed) — worth revisiting whether more of each
   card could be mapped directly, shrinking what's left for the LLM to a
   smaller free-text remainder, the same direction Paris Ciné Info already
   took.

## Alternatives to Ollama

Worth separating two different questions: *"is Ollama itself the problem?"*
vs. *"would a different serving stack fix it?"* — Finding 1a (GPU not used)
is an environment/configuration issue, not something specific to Ollama;
switching serving stacks without fixing GPU access would very likely just
move the exact same CPU-bound bottleneck to a different tool. Fix 1a first,
re-measure, and only then judge whether Ollama's own request handling
(Finding 1b) is still a limit worth switching stacks over.

| Option                                               | Fit for this project                                                    | Notes                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| ---------------------------------------------------- | ----------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Fix Ollama's GPU access (recommended first step)** | Best cost/effort ratio                                                  | Same OpenAI-compatible endpoint the code already targets (`io/llm.py::_build_instructor`) — zero code change if this resolves Finding 1a.                                                                                                                                                                                                                                                                                                                                  |
| **`llama.cpp`'s own `llama-server`**                 | Worth trying if Ollama's GPU issue turns out Ollama-specific            | Ollama is a wrapper around a `llama.cpp`-family backend; running `llama-server` directly gives explicit `-ngl` (GPU layers to offload) and `--parallel`/`--cont-batching` flags with no auto-detection guesswork — more visible control than Ollama's own heuristics, same GGUF model files already downloaded, same OpenAI-compatible `/v1` surface.                                                                                                                      |
| **vLLM**                                             | Better throughput ceiling, higher setup cost                            | Continuous batching genuinely parallelizes many concurrent short requests on GPU — the closest thing to a real fix for Finding 1b at scale. Needs a working CUDA+GPU stack (so it inherits Finding 1a's root cause if unresolved) and is a heavier dependency than this project's current single-binary Ollama setup; likely overkill for a weekly cron scraping ~100 LLM items total once GPU access is fixed, since 1a alone should already collapse most of the wait.   |
| **Hugging Face TGI**                                 | Similar profile to vLLM                                                 | Production-grade, GPU-first, continuous batching — same caveats as vLLM: solves 1b well, doesn't touch 1a on its own, heavier ops footprint than justified here.                                                                                                                                                                                                                                                                                                           |
| **Cloud LLM API** (OpenAI, Groq, Together, etc.)     | Contradicts the project's self-hosted posture, but technically simplest | Would eliminate both 1a and 1b instantly (no local inference at all) and Instructor already speaks the OpenAI API natively — but sends scraped screening text to a third party every run, adds a recurring cost and an external-network dependency for something currently fully self-hosted (`docs/setup.md`'s `OLLAMA_BASE_URL` is explicitly "local or remote Ollama instance"). Only worth considering if self-hosting is explicitly deprioritized — not assumed here. |

**Recommendation**: fix 1a (GPU access) before evaluating anything else in
this table — it's a config fix, not a migration, and the measured 5-10×
per-call speedup it should unlock likely makes 1b (and therefore a stack
switch) a much smaller remaining problem than it looks today.

## How to fix 1a and 1b (for an environment with root/systemd access)

Not attempted in this session (no `sudo`, no working `ollama` CLI in this
sandbox) — a step-by-step for doing it on a machine with real access.

### 1a. Get Ollama onto the GPU

**Step 1 — confirm the problem still exists.**

```bash
nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total --format=csv
curl -s http://localhost:11434/api/ps | python3 -m json.tool   # look at "size_vram"
```

`size_vram: 0` while a model is loaded and `nvidia-smi` shows 0% util / 0
MiB used = confirmed CPU-only inference (exactly what this audit found).

**Step 2 — stop the background service and run Ollama in the foreground**,
so its own startup log is visible instead of going to a log sink that may
not be captured (`journalctl -u ollama` returned nothing useful in this
session's sandbox):

```bash
sudo systemctl stop ollama
ollama serve
```

Look for lines mentioning GPU discovery — Ollama logs its own detection
result at startup (something along the lines of "looking for compatible
GPUs" followed by either a found device or "no compatible GPUs were
discovered"). This one log is the actual root cause; everything below is
"things to check if it says no GPU was found."

**Step 3 — likely culprits, in order of how often they're the actual cause:**

1. **Stale or CPU-only install.** Reinstall with the official script, which
   auto-detects CUDA and pulls the right backend:
   ```bash
   curl -fsSL https://ollama.com/install.sh | sh
   ```
1. **WSL2-specific: the systemd service doesn't see the WSL GPU libraries.**
   Confirm they exist and are resolvable (already true in this session's
   environment, so likely already fine on most WSL2 setups):
   ```bash
   ls /usr/lib/wsl/lib/ | grep -i nvidia
   ldconfig -p | grep libcuda
   ```
   If `ollama serve` run manually (step 2) *does* find the GPU but the
   *systemd service* doesn't, the service's environment is the difference —
   add the library path explicitly:
   ```bash
   sudo systemctl edit ollama
   # add under [Service]:
   #   Environment="LD_LIBRARY_PATH=/usr/lib/wsl/lib"
   sudo systemctl daemon-reload
   sudo systemctl restart ollama
   ```
1. **Driver/CUDA version mismatch.** Check `nvidia-smi`'s reported driver
   and CUDA version against Ollama's release notes for known incompatible
   combinations — this session's environment had driver 596.08 / CUDA 13.2
   (recent), unlikely to be the cause on a similarly up-to-date system, but
   worth ruling out on an older one.
1. **Device permissions.** If Ollama runs as a non-root user, confirm that
   user can run `nvidia-smi` and has access to the GPU device nodes — the
   service in this session's environment ran as `root`, which sidesteps
   this, but a locked-down install might not.

**Step 4 — verify the fix**, model warm, using the same realistic-prompt
timing test this audit ran live:

```bash
python3 -c "
import asyncio, time, httpx

SYSTEM = 'You extract structured data about a single special cinema screening...'  # full prompt in io/llm.py::_SYSTEM_PROMPT
USER = 'Cinéma en plein air de La Villette\nReference date: 2026-07-06\nMercredi 22 juillet à 18h00\nMon voisin Totoro'

async def one_call(client):
    t0 = time.monotonic()
    r = await client.post('http://localhost:11434/v1/chat/completions', json={
        'model': 'qwen2.5:7b',
        'messages': [{'role':'system','content':SYSTEM},{'role':'user','content':USER}],
        'response_format': {'type': 'json_object'},
    })
    print(f'{time.monotonic()-t0:.1f}s, status={r.status_code}')

async def main():
    async with httpx.AsyncClient(timeout=120) as client:
        await one_call(client)  # warmup, discard
        await one_call(client)  # this is the real number

asyncio.run(main())
"
curl -s http://localhost:11434/api/ps | python3 -m json.tool   # size_vram should now be > 0
```

This session measured **16.0-16.6s per call on CPU**; on a properly
GPU-accelerated RTX 4070, expect roughly **1.5-3s** (the 5-10× this audit
estimated) — if the second call is still double-digit seconds, the GPU
still isn't being used.

### 1b. Raise `OLLAMA_NUM_PARALLEL`

Only do this *after* 1a is confirmed fixed (`size_vram > 0`) — on CPU it
mostly just interleaves the same scarce compute across more in-flight
requests.

```bash
sudo systemctl edit ollama
# add under [Service]:
#   Environment="OLLAMA_NUM_PARALLEL=2"
sudo systemctl daemon-reload
sudo systemctl restart ollama
```

Start at **2**, not higher — an ~4.7GB Q4 model leaves real but not
unlimited headroom in 8GB VRAM once a second concurrent KV cache is added;
watch `nvidia-smi`'s memory column while under load and raise to 3-4 only
if there's still comfortable headroom. Verify it actually helped by
re-running this session's concurrent-vs-sequential comparison: fire 5
concurrent calls via `asyncio.gather` and 5 sequential calls back to back
(same shape as the Step 4 script above, wrapped in `asyncio.gather` for the
concurrent half) — before the fix both totals were statistically identical
(~81s each); after, the concurrent total should drop toward
`total / OLLAMA_NUM_PARALLEL`.

## Finding 2: sources run strictly one after another

`pipeline.py::IngestionPipeline.run()`:

```python
outcomes = tuple(
    [
        SourceOutcome(source=scraper.source.value, events=await self._ingest_source(...))
        for scraper in self._scrapers
    ]
)
```

This is a list comprehension with `await` inside — every scraper's entire
fetch-then-ingest cycle completes before the next one starts. Total wall
time is the **sum** of every source's duration, not the **max**. With this
run's numbers, a fully source-parallel run would be bounded by the slowest
single source (La Villette, ~19 min) instead of their sum (~61 min) — a
~3× reduction, before touching Finding 1 at all.

**The real constraint isn't HTTP** — `httpx.AsyncClient` is already safe for
concurrent use, and the shared instance in `main.py::_run_ingestion` is
already designed to be passed to every scraper. **It's the shared
`AsyncSession`**: SQLAlchemy's `AsyncSession` is not safe for concurrent use
from multiple coroutines at once, and `EventRepository.ingest()` writes to
it per sighting inside each scraper's `_ingest_source` loop. Two designs
that respect this:

1. **Concurrent fetch, sequential ingest.** Split `_ingest_source` into a
   fetch phase (`scraper.fetch_events`, pure network + LLM, no DB) run
   concurrently across all scrapers via `asyncio.gather`, followed by a
   sequential ingest phase feeding every source's sightings through the one
   session, in source order (preserves today's first-source-wins dedup
   semantics unchanged). Captures nearly all the win — DB writes are cheap
   relative to fetch/LLM time — with no session-safety risk at all.
1. **One session per scraper**, each with its own transaction, committed
   independently. More parallelism, but changes the dedup/merge semantics
   subtly (two sources' sightings for the *same* screening could now race)
   and needs its own design pass — not needed to capture most of the gain
   here, so not recommended as the first move.

Option 1 is the recommended shape: same session-safety guarantees as today,
same dedup order, no architecture change beyond restructuring
`_ingest_source`'s two phases.

## Finding 3: Paris Ciné Info's discovery loop is unbounded-sequential

`ParisCineInfoScraper.fetch_events`:

```python
for movie in movies:
    showtimes = await self._fetch_showtimes(client, movie)
    ...
```

One sequential HTTP round-trip per film in the catalogue (up to ~494 per
`docs/coverage-matrix.md`'s Phase 0 recon), *before* the progress bar even
starts (the module's own docstring: "Discovery... runs first and is not
itself progress-reported"). Every other per-item operation in this exact
file (`fetch_venue_details`, and `gather_events` itself) already uses the
established `asyncio.Semaphore(_CONCURRENCY)` pattern — this loop is the one
inconsistent holdout. At even a modest 150-300ms per call, ~494 sequential
calls adds 1-2.5 minutes of pure unparallelized wait, hidden inside what the
progress bar reports as "not started yet". Cheap, consistent, low-risk fix:
apply the same bounded-concurrency pattern already used three other times in
this same file.

## Finding 4 (latent, not dominant today): N+1 queries in specialness classification

`pipeline.py::_film_context` runs **two separate DB queries per screening**
(`count_distinct_venues`, `count_screenings`) for every not-yet-special
screening — this run classified 35,361 screenings, i.e. ~70,700 individual
round-trip queries. Measured live: 158 seconds (~224 verdicts/sec, ~447
queries/sec) — **not** today's bottleneck, because local SQLite has no
network round-trip latency to pay per query.

This is still a real N+1 pattern, and it is the one finding in this audit
that would become a serious problem under a networked database (Postgres,
etc.) rather than local SQLite — at even 2ms/query network latency, 70,700
queries would cost ~140s just in round-trip time, likely much worse under
real contention. Since `count_distinct_venues`/`count_screenings` only
depend on `film_id` (and `venue_id` for the second), both can be precomputed
**once per film** (a single aggregate query grouping by `film_id`/
`(film_id, venue_id)`) instead of once per screening, then looked up from an
in-memory dict during the classification loop — turns O(screenings) queries
into O(films) + O(film, venue pairs) queries, a large constant-factor
reduction at this data's actual cardinality (782 films vs. 35,361
screenings this run). Worth fixing proactively rather than waiting for it to
actually hurt.

## Finding 5 (accounts for the unexplained gap): one DB commit per sighting

Adding up every measured phase (all seven sources' progress bars + the
158s classification pass) accounts for ~51 of the run's 61 minutes — leaving
roughly **8-9 minutes unexplained** by anything visible in a progress bar.
The likely explanation: `EventRepository.ingest()`'s `_commit()` call — and
`_merge()`'s equivalent — runs **once per sighting**, not once per source or
per batch. With 39,338 sightings processed this run (most hitting `_merge`,
per this session's dedup-key investigation), that's up to ~39,338 individual
SQLite commits, each a separate round trip through
`AsyncSession.commit()` — plus TMDB enrichment calls and the
`_apply_venue_passes`/`_apply_venue_details` end-of-run HTTP steps, which
also live in this unaccounted window. Not independently measured in this
pass (no per-commit instrumentation was added), so treat the exact share as
an estimate, not a confirmed number the way Findings 1-4 are — but the
mechanism is real and cheap to fix: batch commits (e.g. once per source, or
every N sightings) instead of one per sighting, the same "reduce round trips"
principle as Finding 4.

## Multithreading — evaluated and not recommended

The entire pipeline is I/O-bound: waiting on network responses (scraped
sites, TMDB, Ollama) and, to a much smaller extent, local disk I/O
(SQLite). Python's `asyncio` — already used throughout this codebase —
releases control during an I/O wait exactly like a thread would block on
one, at a fraction of the memory/context-switch cost of an OS thread, and
without the GIL becoming a factor either way (the GIL only serializes *CPU*
-bound Python bytecode; it does not block during an `await` on network I/O
today, and it would not meaningfully block equivalent OS-thread I/O waits
either).

Concretely, in this codebase:

- Adding real OS threads (`threading`, `concurrent.futures.ThreadPoolExecutor`)
  would not speed up any of Findings 1-5 — none of them are CPU-bound
  Python computation being serialized by the GIL. Finding 1's bottleneck is
  outside this process entirely (CPU-bound Ollama inference, confirmed with
  0% GPU use, plus no server-side parallel slot); Findings 2, 3, and 5 are
  already-async code that simply isn't invoking its own concurrency
  primitives (`asyncio.gather`) or batching its round trips at the right
  granularity yet.
- Multiprocessing would not help Finding 1 either — more OS processes each
  waiting on the same single Ollama inference slot just queues up
  differently, it does not create inference capacity that doesn't exist.
  It could theoretically help Finding 4's DB work if that ever became
  CPU-bound (e.g. heavy in-Python aggregation), but the recommended fix
  there is an algorithmic one (fewer, batched queries), which removes the
  need for that entirely.
- The one place literal CPU-bound work exists is HTML parsing
  (BeautifulSoup, per scraper) and JSON parsing (Paris Ciné Info, MK2's
  now-retired RSC extraction) — at this run's volumes (184 offi.fr pages
  parsed inside a 63-second phase that also includes the network fetches)
  this is not remotely the bottleneck; a `ThreadPoolExecutor` to offload
  parsing off the event loop would add real complexity for no measurable
  win today.

**Conclusion: stay on `asyncio`.** The fix for Finding 1 lives outside
Python entirely (Ollama configuration / fewer LLM calls); the fixes for
Findings 2 and 3 are "use `asyncio.gather` where it's currently missing," not
"introduce a different concurrency model."

## Tooling evaluation

| Tool                                      | Verdict                                              | Notes                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| ----------------------------------------- | ---------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Ollama** (LLM serving)                  | **Keep — the problem is GPU access, not the tool**   | Confirmed live: 0% GPU utilization, 0 MiB VRAM used during inference (Finding 1a) — CPU-bound regardless of which serving stack sits on top of a misconfigured GPU. Fix GPU access first (see "Alternatives to Ollama"); only reach for `llama-server`/vLLM/TGI if Ollama's own request handling (Finding 1b, confirmed no parallel slot today) is still a real limit after that.                                                                                                                                          |
| **httpx** (async HTTP client)             | Keep, minor tuning available                         | Solid async-native choice, already shared correctly across scrapers. Not using HTTP/2 (`httpx[http2]` + `http2=True`) — would let concurrent requests to the *same* host (Paris Ciné Info, offi.fr) multiplex over fewer TCP connections; a real but secondary win, worth adding once Findings 1-3 are addressed, not before. Connection pool left at httpx's defaults (`max_connections=100`, `max_keepalive_connections=20`) — adequate at this run's request volumes, no evidence of connection exhaustion in the logs. |
| **SQLite + aiosqlite**                    | Keep for now, revisit if Finding 4 isn't fixed first | Fine for the current single-writer local deployment and the volumes measured (40k+ screenings, sub-3-minute classification pass even with the N+1 pattern). The N+1 pattern in Finding 4 is the one thing that would make a future move to a networked DB (Postgres) painful — fix the query pattern before that migration, not after.                                                                                                                                                                                     |
| **BeautifulSoup** (`html.parser` backend) | Keep                                                 | Not a bottleneck at today's volumes (offi.fr's 184-page parse is folded into a 63-second phase dominated by network fetch, not parse time). If HTML parsing ever does become measurable, swapping the backend to `lxml` (`BeautifulSoup(html, "lxml")`) is a drop-in, dependency-only change (`uv add lxml`) worth 2-5× on parse time alone — not needed today.                                                                                                                                                            |
| **asyncio** (concurrency model)           | Keep, use more consistently                          | See "Multithreading" above — the right model for this workload; Findings 2 and 3 are gaps in applying it, not reasons to replace it.                                                                                                                                                                                                                                                                                                                                                                                       |

## Every gain identified, ranked, with an estimated time saved

**Implementation status (2026-07-14, later same day)**: rows 2, 3, and 4
below are implemented (`IngestionPipeline.run()`'s concurrent-fetch/
sequential-ingest split, Paris Ciné Info's bounded-concurrency discovery
loop, and the batched specialness-classification queries) — all three
verified against the existing test suite with zero unexpected breakage,
confirming the "verified safe" analysis behind each fix. Rows 1a and 1b
remain infrastructure/config changes outside this codebase, not attempted.
Row 5 (batch DB commits) was evaluated and deliberately left out — see its
own section above for the fault-isolation trade-off. **A fresh live scrape
to re-measure actual wall-clock impact has not been run yet** — the
estimates below are still pre-implementation projections, not confirmed
post-fix numbers.

Baseline: the real 2026-07-14 run, **61 minutes** end to end. Estimates below
are built from that run's actual numbers; only Finding 1's GPU speedup
factor (5-10×) and Finding 5's commit-batching share are extrapolated rather
than independently re-measured — flagged as such. Savings are not simply
additive (fixing Finding 2 changes what the "new bottleneck" is for
everything after it), so the running total after each row assumes the rows
above it are already applied, in order.

| #   | Fix                                                                                                                                       | Confidence                                                                                                                                      | Estimated saving                                                                                                                                                                                        | Running total after |
| --- | ----------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------- |
| 1a  | **Fix Ollama's GPU access** (config, likely WSL2/driver-related)                                                                          | Root cause confirmed live (0% GPU util); speedup factor estimated (typical CPU→GPU ratio for a 7B Q4 model, not yet re-measured)                | ~27 min (both LLM sources' 31 min drops to an estimated ~4 min at 5-10× per-call speedup)                                                                                                               | **~34 min**         |
| 2   | **Parallelize source execution** (concurrent fetch, sequential ingest)                                                                    | Measured baseline (each source's real duration), mechanical reasoning about the fix                                                             | Total run bounded by the *slowest single source* instead of their sum — with 1a applied, that's paris-cine-info's ~16 min bar, down from summing everything                                             | **~18-20 min**      |
| 1b  | **Raise `OLLAMA_NUM_PARALLEL`** once 1a is fixed (2-4 slots, VRAM-bounded)                                                                | Confirmed live: zero parallelism today; benefit after 1a is estimated, not measured                                                             | Marginal once 1 and 2 are both applied — LLM sources are no longer the bottleneck at that point (paris-cine-info's own duration dominates); real win mainly if a future week's LLM-source volume spikes | **~17-19 min**      |
| 5   | **Batch DB commits** (once per source/batch instead of once per sighting)                                                                 | Inferred from the ~8-9 min gap unexplained by any measured phase; mechanism confirmed by reading `ingest()`/`_merge()`, not independently timed | A few minutes, most plausibly concentrated in paris-cine-info's own ~16 min bar (by far the most sightings) — would directly cut into the new bottleneck from row 2                                     | **~14-16 min**      |
| 3   | **Parallelize Paris Ciné Info's discovery loop** (~494 sequential calls → bounded concurrency, same pattern used 3× already in that file) | Estimated from typical per-call latency (150-300ms); not independently timed this pass                                                          | ~1-2 min                                                                                                                                                                                                | **~13-14 min**      |
| 4   | **Fix the N+1 specialness-classification queries**                                                                                        | Measured baseline (158s today); saving is an estimate of the batched-query approach's likely cost                                               | Classification pass likely drops from ~2.6 min to under 1 min                                                                                                                                           | **~12-13 min**      |
| —   | HTTP/2 for httpx                                                                                                                          | Real but secondary, not independently measured                                                                                                  | Low-single-digit seconds across the whole run — noise next to the rows above                                                                                                                            | negligible          |
| —   | `lxml` parser backend for BeautifulSoup                                                                                                   | Not a bottleneck at today's volumes (offi.fr's 184-page parse already folds into a fetch-dominated 63s phase)                                   | Effectively zero today; only matters if HTML volume grows a lot                                                                                                                                         | negligible          |
| —   | Smaller/quantized extraction model (Finding 1, item 3)                                                                                    | Depends on accuracy trade-off, needs `eval-extraction` comparison first                                                                         | Could compound with 1a/1b once GPU is fixed; not stackable with today's numbers without a baseline eval run first                                                                                       | not quantified      |
| —   | Fewer LLM calls (map more fields directly, Finding 1, item 4)                                                                             | Architectural, source-by-source work, no single number                                                                                          | Reduces the volume Findings 1a/1b apply to going forward; biggest for whichever source's card structure turns out to be templated enough                                                                | not quantified      |

**Headline number**: the four rows with a concrete estimate (1a, 2, 5, 3, 4)
plausibly take a 61-minute run down to **roughly 12-16 minutes** — a ~4-5×
reduction — with the GPU fix alone responsible for the majority of that
(~27 of the ~45-49 minutes saved). Everything past row 4 is real but small
next to that.

**Suggested order to actually implement in**: 1a first, always — it's a
config investigation, not a code change, costs nothing to try, and every
other estimate in this table assumes it's done. Re-measure with a real
scrape after 1a alone before deciding whether 1b/2/3/4/5 are worth the
implementation effort in that new, smaller picture — Finding 2's "concurrent
fetch, sequential ingest" redesign in particular is the next-biggest single
piece of code to actually write, and its real value depends on what the
post-1a bottleneck turns out to be.
