# Market Intel Candidate Hypothesis Validation

Frozen candidates, no threshold tuning:

- `long_oi_down_price_up`: side=long, OI down, price up
- `short_oi_up_price_down`: side=short, OI up, price down
- `abs_funding_z_ge_2`: abs(funding_zscore) >= 2

| candidate | count | mean | median | win_rate | baseline_win_rate | total | avg_holding_bars | passes_min_count | mean_negative | median_nonpositive | win_rate_below_baseline | total_negative | first_half_count | first_half_mean | second_half_count | second_half_mean | vol_only_mean | vol_only_median | vol_only_win_rate | random_mean_min | random_mean_avg | random_mean_max | random_as_bad_or_worse | random_trials |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| long_oi_down_price_up | 5 | 1.0714 | 0.5012 | 0.6000 | 0.4550 | 5.3568 | 3.2000 | 0 | 0 | 0 | 0 | 0 | 5 | 1.0714 | 0 | nan | 19.9848 | 7.4961 | 0.6000 | -32.0812 | -0.9168 | 38.2975 | 60 | 100 |
| short_oi_up_price_down | 6 | -2.1011 | -4.0189 | 0.3333 | 0.4550 | -12.6064 | 1.1667 | 0 | 1 | 1 | 1 | 1 | 6 | -2.1011 | 0 | nan | 18.6936 | 9.8668 | 0.6667 | -33.5537 | -1.6651 | 35.4964 | 49 | 100 |
| abs_funding_z_ge_2 | 38 | -7.9989 | -9.6100 | 0.3158 | 0.4550 | -303.9579 | 1.0000 | 1 | 1 | 1 | 1 | 1 | 20 | -17.0079 | 18 | 2.0111 | 3.7778 | 11.3647 | 0.5789 | -13.0574 | -0.7410 | 12.4598 | 6 | 100 |

Rule of thumb: a candidate is not strong if same-count random buckets often match or exceed its weakness.