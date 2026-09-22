# Diagnostics

Scripts that **measure** the complaints ("the bot freezes") instead of arguing
about them. They drive the real production dispatcher through
`tests/harness.py`, so what they report is what the bot actually does.

Run them from the repository root with the project's virtualenv:

```bash
.venv/bin/python diagnostics/silence_audit.py     # 90 cases, expects silent=0
.venv/bin/python diagnostics/repro_mw.py          # per-user serialization + album
.venv/bin/python diagnostics/repro_slowdisk.py    # stalled download / slow volume
.venv/bin/python diagnostics/perf_ab.py           # connection reuse A/B
.venv/bin/python diagnostics/perf_accept.py       # Accept: answer + ticket latency
```

| script | what it proves |
|---|---|
| `silence_audit.py` | drives 9 form steps x 9 input kinds, plus the same inputs with no active state, and counts updates that get **no answer at all**. The `Update ... is handled. Duration 1 ms` log line comes from exactly these. |
| `repro_mw.py` | 20 simultaneous updates for one user never run in parallel (and 20 users still do); an album of 4 photos keeps all 4. |
| `repro_slowdisk.py` | a stalled photo download used to hold the participant's next message for 4.81 s (now bounded, and they are asked to resend); slow volume writes used to queue SQLite queries for up to 750.7 ms on the shared default pool. |
| `perf_ab.py` | one four-step registration: 2 SQLite connections instead of 38, 21 ms instead of 183 ms (8.8x) with a 4 ms pragma cost. |
| `perf_accept.py` | Accept from the moderation chat on an emulated 500 KB/s uplink, with a 3.9 MB phone photo as the poster hero. Before: `AnswerCallbackQuery` went out **last**, at 6.63 s, after a 2.97 MB PNG upload. After: the tap is answered at 0.01 s and the ticket lands at 0.74 s — a 0.54 MB JPEG — and the card ends with «🎫 Билет отправлен участнику». `--bps` changes the link speed. |

They are development tools, not part of the deployed bot: nothing under
`bot/` imports them.
