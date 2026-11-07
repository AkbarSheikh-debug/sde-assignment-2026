# Grade report — public_03_load

## Outcomes vs baseline (no-op controller)

| workflow | conclusion rate | Δ | pickup | Δ | engagement | Δ | engagement gap captured | hallucinated calls (base) |
|---|---|---|---|---|---|---|---|---|
| arav_cancel_2 | 0.368 | +9.1% | 0.261 | +7.5% | 0.500 | +0.8% | 5% | 460 (409) |
| arav_kyc_1 | 0.478 | +33.1% | 0.275 | +27.7% | 0.599 | -0.3% | 6% | 473 (352) |
| arav_renewal_0 | 0.443 | +9.3% | 0.282 | +6.7% | 0.559 | +2.0% | 10% | 464 (408) |
| kest_cancel_2 | 0.368 | +9.3% | 0.262 | +7.9% | 0.501 | +0.4% | 2% | 438 (418) |
| kest_kyc_1 | 0.421 | +18.3% | 0.247 | +15.1% | 0.598 | +0.1% | 1% | 392 (370) |
| kest_renewal_0 | 0.440 | +9.5% | 0.281 | +6.1% | 0.555 | +1.9% | 11% | 449 (403) |
| nort_cancel_2 | 0.366 | +7.8% | 0.261 | +6.8% | 0.499 | +0.2% | 0% | 424 (368) |
| nort_kyc_1 | 0.440 | +23.8% | 0.255 | +18.8% | 0.598 | +0.4% | 2% | 414 (318) |
| nort_renewal_0 | 0.454 | +12.0% | 0.281 | +6.5% | 0.573 | +4.5% | 21% | 483 (419) |
| tide_cancel_2 | 0.362 | +5.5% | 0.260 | +6.2% | 0.497 | -0.9% | 1% | 400 (401) |
| tide_kyc_1 | 0.480 | +34.8% | 0.274 | +27.5% | 0.602 | +0.7% | 6% | 434 (376) |
| tide_renewal_0 | 0.449 | +10.9% | 0.282 | +7.0% | 0.562 | +2.9% | 13% | 486 (425) |
| ALL | 0.422 | +15.2% | 0.268 | +11.4% | 0.554 | +1.5% | 7% | 5317 (4667) |

## Changes applied by the controller

84 applied — neutral: 49, beneficial: 35

- arav_cancel_2 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.006 → **neutral**
- arav_renewal_0 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.008 → **neutral**
- kest_cancel_2 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.006 → **neutral**
- kest_renewal_0 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.008 → **neutral**
- nort_cancel_2 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.006 → **neutral**
- nort_renewal_0 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.008 → **neutral**
- tide_cancel_2 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.006 → **neutral**
- tide_renewal_0 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.008 → **neutral**
- nort_renewal_0 v3 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.052 → **beneficial**
- arav_renewal_0 v3 (controller_approved) dims=['voice'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- kest_cancel_2 v3 (controller_approved) dims=['voice', 'caller_id'] engageΔ=+0.000 didΔ=+0.039 → **beneficial**
- tide_renewal_0 v3 (controller_approved) dims=['voice'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- kest_renewal_0 v3 (controller_approved) dims=['voice', 'caller_id'] engageΔ=+0.000 didΔ=+0.039 → **beneficial**
- arav_cancel_2 v3 (controller_approved) dims=['voice'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- tide_cancel_2 v3 (controller_approved) dims=['voice', 'caller_id'] engageΔ=+0.000 didΔ=+0.042 → **beneficial**
- arav_cancel_2 v4 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.028 → **beneficial**
- arav_renewal_0 v4 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.057 → **beneficial**
- kest_renewal_0 v4 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.016 → **neutral**
- tide_renewal_0 v4 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.057 → **beneficial**
- nort_cancel_2 v3 (controller_approved) dims=['voice', 'caller_id'] engageΔ=+0.000 didΔ=+0.042 → **beneficial**
- arav_kyc_1 v2 (controller_approved) dims=['voice', 'caller_id'] engageΔ=+0.004 didΔ=+0.252 → **beneficial**
- arav_cancel_2 v5 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.013 → **neutral**
- tide_kyc_1 v2 (controller_approved) dims=['voice', 'caller_id'] engageΔ=+0.004 didΔ=+0.260 → **beneficial**
- nort_renewal_0 v4 (controller_approved) dims=['voice'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- kest_cancel_2 v4 (controller_auto) dims=['voice'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- tide_renewal_0 v5 (controller_approved) dims=['welcome_message'] engageΔ=+0.003 didΔ=+0.000 → **neutral**
- nort_kyc_1 v2 (controller_approved) dims=['voice', 'caller_id'] engageΔ=+0.004 didΔ=+0.213 → **beneficial**
- nort_renewal_0 v5 (controller_approved) dims=['welcome_message'] engageΔ=+0.003 didΔ=+0.000 → **neutral**
- nort_cancel_2 v4 (controller_approved) dims=['welcome_message'] engageΔ=+0.002 didΔ=+0.000 → **neutral**
- arav_cancel_2 v6 (controller_approved) dims=['welcome_message'] engageΔ=+0.002 didΔ=+0.000 → **neutral**
- arav_renewal_0 v5 (controller_approved) dims=['welcome_message'] engageΔ=+0.003 didΔ=+0.000 → **neutral**
- kest_cancel_2 v5 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.002 didΔ=+0.000 → **neutral**
- arav_kyc_1 v3 (controller_approved) dims=['welcome_message', 'caller_id'] engageΔ=+0.004 didΔ=+0.124 → **beneficial**
- kest_renewal_0 v5 (controller_approved) dims=['welcome_message'] engageΔ=+0.003 didΔ=+0.000 → **neutral**
- kest_kyc_1 v2 (controller_approved) dims=['voice', 'caller_id'] engageΔ=+0.004 didΔ=+0.272 → **beneficial**
- tide_kyc_1 v3 (controller_approved) dims=['welcome_message', 'caller_id'] engageΔ=+0.004 didΔ=+0.133 → **beneficial**
- nort_renewal_0 v6 (controller_approved) dims=['voice'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- kest_cancel_2 v6 (controller_auto) dims=['welcome_message'] engageΔ=+0.002 didΔ=+0.000 → **neutral**
- kest_cancel_2 v7 (controller_auto) dims=['welcome_message'] engageΔ=+0.003 didΔ=+0.000 → **neutral**
- tide_kyc_1 v4 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.111 → **beneficial**
- arav_kyc_1 v4 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.111 → **beneficial**
- tide_renewal_0 v6 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.004 didΔ=+0.000 → **neutral**
- nort_kyc_1 v3 (controller_approved) dims=['welcome_message', 'caller_id'] engageΔ=+0.004 didΔ=+0.116 → **beneficial**
- arav_renewal_0 v6 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.004 didΔ=+0.000 → **neutral**
- kest_cancel_2 v8 (controller_approved) dims=['voice'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- tide_cancel_2 v4 (controller_approved) dims=['welcome_message'] engageΔ=+0.002 didΔ=+0.000 → **neutral**
- nort_cancel_2 v5 (controller_approved) dims=['voice'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- kest_renewal_0 v6 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.040 didΔ=+0.000 → **beneficial**
- nort_renewal_0 v7 (controller_approved) dims=['welcome_message'] engageΔ=+0.007 didΔ=+0.000 → **neutral**
- arav_cancel_2 v7 (controller_approved) dims=['voice'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- nort_kyc_1 v4 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.139 → **beneficial**
- tide_renewal_0 v7 (controller_approved) dims=['welcome_message'] engageΔ=+0.011 didΔ=+0.000 → **beneficial**
- kest_cancel_2 v9 (controller_auto) dims=['voice'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- nort_cancel_2 v6 (controller_auto) dims=['voice'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- arav_renewal_0 v7 (controller_approved) dims=['cohorts', 'welcome_message'] engageΔ=+0.022 didΔ=+0.000 → **beneficial**
- arav_cancel_2 v8 (controller_auto) dims=['voice'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- nort_cancel_2 v7 (controller_auto) dims=['voice'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- kest_kyc_1 v3 (controller_approved) dims=['welcome_message', 'caller_id'] engageΔ=+0.004 didΔ=+0.099 → **beneficial**
- arav_renewal_0 v8 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.002 didΔ=+0.000 → **neutral**
- nort_renewal_0 v8 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.103 didΔ=+0.000 → **beneficial**
- tide_renewal_0 v8 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.011 didΔ=+0.000 → **beneficial**
- tide_cancel_2 v5 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.002 didΔ=+0.000 → **neutral**
- nort_cancel_2 v8 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=-0.005 didΔ=+0.000 → **neutral**
- arav_cancel_2 v9 (controller_approved) dims=['welcome_message'] engageΔ=+0.018 didΔ=+0.000 → **beneficial**
- kest_cancel_2 v10 (controller_approved) dims=['welcome_message'] engageΔ=-0.005 didΔ=+0.000 → **neutral**
- kest_renewal_0 v7 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.005 didΔ=+0.000 → **neutral**
- arav_kyc_1 v5 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.066 didΔ=+0.000 → **beneficial**
- tide_kyc_1 v5 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.061 didΔ=+0.000 → **beneficial**
- nort_renewal_0 v9 (controller_approved) dims=['welcome_message'] engageΔ=+0.011 didΔ=+0.000 → **beneficial**
- kest_cancel_2 v11 (controller_auto) dims=['welcome_message'] engageΔ=+0.005 didΔ=+0.000 → **neutral**
- nort_cancel_2 v9 (controller_auto) dims=['welcome_message'] engageΔ=+0.002 didΔ=+0.000 → **neutral**
- nort_cancel_2 v10 (controller_auto) dims=['welcome_message'] engageΔ=+0.003 didΔ=+0.000 → **neutral**
- arav_renewal_0 v9 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.006 didΔ=+0.000 → **neutral**
- tide_renewal_0 v9 (controller_approved) dims=['welcome_message'] engageΔ=+0.076 didΔ=+0.000 → **beneficial**
- nort_cancel_2 v11 (controller_approved) dims=['voice'] engageΔ=+0.004 didΔ=+0.000 → **neutral**
- kest_cancel_2 v12 (controller_approved) dims=['voice'] engageΔ=+0.004 didΔ=+0.000 → **neutral**
- tide_cancel_2 v6 (controller_approved) dims=['welcome_message'] engageΔ=-0.003 didΔ=+0.000 → **neutral**
- tide_renewal_0 v10 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.018 didΔ=+0.000 → **beneficial**
- arav_renewal_0 v10 (controller_approved) dims=['welcome_message'] engageΔ=+0.044 didΔ=+0.000 → **beneficial**
- nort_kyc_1 v5 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.061 didΔ=+0.000 → **beneficial**
- arav_cancel_2 v10 (controller_approved) dims=['voice'] engageΔ=+0.004 didΔ=+0.000 → **neutral**
- arav_kyc_1 v6 (controller_approved) dims=['voice'] engageΔ=+0.050 didΔ=+0.000 → **beneficial**
- tide_kyc_1 v6 (controller_approved) dims=['welcome_message'] engageΔ=+0.055 didΔ=+0.000 → **beneficial**
- arav_cancel_2 v11 (controller_auto) dims=['welcome_message', 'voice'] engageΔ=+0.012 didΔ=+0.000 → **beneficial**

## Governance

```
{
  "approval_requests": 69,
  "decisions": {
    "approved": 60,
    "rejected": 8,
    "expired": 0
  },
  "rejection_reasons": {
    "approver declined (judgement call)": 8
  },
  "applied_via_approval": 59,
  "auto_applied": 25,
  "auto_apply_rejected": 0,
  "apply_conflicts": 0,
  "lapsed": 0,
  "peak_requests_per_24h": {
    "arav_kyc_1": 2,
    "arav_renewal_0": 2,
    "nort_kyc_1": 2,
    "nort_renewal_0": 2,
    "tide_kyc_1": 2,
    "tide_renewal_0": 2,
    "arav_cancel_2": 1,
    "kest_cancel_2": 1,
    "kest_kyc_1": 1,
    "kest_renewal_0": 1,
    "nort_cancel_2": 1,
    "tide_cancel_2": 1
  }
}
```

## Incidents

```
[]
```

## Decaying variants — share of in-population calls still on the variant, by day after onset

```
{}
```

Credits used: 5 · validation runs: 1 · LLM fix calls: 0