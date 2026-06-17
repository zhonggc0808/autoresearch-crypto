# Market Intel Candidate Hypothesis Validation

Frozen candidates, no threshold tuning:

- `long_oi_down_price_up`: side=long, OI down, price up
- `short_oi_up_price_down`: side=short, OI up, price down
- `abs_funding_z_ge_2`: abs(funding_zscore) >= 2

| candidate | count | mean | median | win_rate | baseline_win_rate | total | avg_holding_bars | passes_min_count | mean_negative | median_nonpositive | win_rate_below_baseline | total_negative | first_half_count | first_half_mean | second_half_count | second_half_mean | vol_only_mean | vol_only_median | vol_only_win_rate | random_mean_min | random_mean_avg | random_mean_max | random_as_bad_or_worse | random_trials |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| long_oi_down_price_up | 13 | -25.7615 | -20.0540 | 0.0769 | 0.4185 | -334.9000 | 2.0000 | 0 | 1 | 1 | 1 | 1 | 8 | -13.4834 | 5 | -45.4065 | 21.9908 | -4.1970 | 0.4615 | -23.1431 | -2.7158 | 16.0236 | 0 | 100 |
| short_oi_up_price_down | 15 | -10.7217 | -2.3244 | 0.4000 | 0.4185 | -160.8259 | 1.0000 | 0 | 1 | 1 | 1 | 1 | 7 | -8.2162 | 8 | -12.9141 | 22.2051 | 9.7472 | 0.5333 | -24.1505 | -3.2858 | 14.6265 | 13 | 100 |
| abs_funding_z_ge_2 | 28 | -4.2548 | -4.1878 | 0.4643 | 0.4185 | -119.1346 | 1.0000 | 1 | 1 | 1 | 0 | 1 | 0 | nan | 28 | -4.2548 | 2.3414 | -9.2633 | 0.3929 | -15.5621 | -3.3161 | 5.7406 | 36 | 100 |

Rule of thumb: a candidate is not strong if same-count random buckets often match or exceed its weakness.