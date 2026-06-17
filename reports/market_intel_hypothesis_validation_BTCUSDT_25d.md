# Market Intel Candidate Hypothesis Validation

Frozen candidates, no threshold tuning:

- `long_oi_down_price_up`: side=long, OI down, price up
- `short_oi_up_price_down`: side=short, OI up, price down
- `abs_funding_z_ge_2`: abs(funding_zscore) >= 2

| candidate | count | mean | median | win_rate | baseline_win_rate | total | avg_holding_bars | passes_min_count | mean_negative | median_nonpositive | win_rate_below_baseline | total_negative | first_half_count | first_half_mean | second_half_count | second_half_mean | vol_only_mean | vol_only_median | vol_only_win_rate | random_mean_min | random_mean_avg | random_mean_max | random_as_bad_or_worse | random_trials |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| long_oi_down_price_up | 17 | -9.6962 | -11.0078 | 0.2941 | 0.3790 | -164.8361 | 1.2353 | 0 | 1 | 1 | 1 | 1 | 13 | -6.2045 | 4 | -21.0444 | -13.2219 | -12.0098 | 0.3529 | -14.7615 | -2.8997 | 9.6492 | 6 | 100 |
| short_oi_up_price_down | 8 | -3.2218 | -6.1380 | 0.3750 | 0.3790 | -25.7744 | 1.2500 | 0 | 1 | 1 | 1 | 1 | 7 | -4.7566 | 1 | 7.5218 | -20.5606 | -13.6182 | 0.3750 | -19.0164 | -3.7052 | 13.1009 | 54 | 100 |
| abs_funding_z_ge_2 | 18 | -0.4352 | -4.8950 | 0.3889 | 0.3790 | -7.8339 | 1.0000 | 0 | 1 | 1 | 0 | 1 | 10 | -5.7115 | 8 | 6.1602 | -12.9316 | -11.2442 | 0.3333 | -16.3564 | -2.8064 | 9.6869 | 70 | 100 |

Rule of thumb: a candidate is not strong if same-count random buckets often match or exceed its weakness.