# Measured results

Three repeats per setting. Latency and throughput values below are medians of trial summaries, not pooled percentiles.
Worker plots show the min/max throughput over three runs. API plots show min/max trial p95; these are not confidence intervals.

| Worker maximum | Observed peak per repeat | Median seconds | Rows/s | Speedup | Efficiency vs maximum |
| --- | --- | ---: | ---: | ---: | ---: |
| 1 | [1, 1, 1] | 26.30 | 6083 | 1.00 | 100.0% |
| 2 | [2, 2, 2] | 18.69 | 8560 | 1.41 | 70.4% |
| 4 | [4, 4, 4] | 17.66 | 9062 | 1.49 | 37.2% |
| 8 | [8, 8, 8] | 16.90 | 9468 | 1.56 | 19.5% |

| Users | Mode | Requests | Failures | Median successful req/s | Median p50 ms | Median p95 ms |
| ---: | --- | ---: | ---: | ---: | ---: | ---: |
| 1 | cached | 551 | 0 | 6.18 | 40 | 120 |
| 1 | bypass | 67 | 0 | 0.83 | 960 | 2700 |
| 5 | cached | 2575 | 0 | 28.48 | 48 | 160 |
| 5 | bypass | 87 | 24 | 0.73 | 5100 | 6700 |
| 10 | cached | 5057 | 0 | 56.36 | 57 | 150 |
| 10 | bypass | 191 | 151 | 0.36 | 5100 | 13000 |
| 20 | cached | 9159 | 0 | 104.92 | 66 | 220 |
| 20 | bypass | 355 | 314 | 0.33 | 5100 | 11000 |
