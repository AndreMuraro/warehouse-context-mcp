## Results: openrouter:kimi-k3

20 questions, 3 run(s) per arm; counts are over all runs. A wrong-table pick is a wrong answer from a run that read a decoy; reading one while checking or rebuilding the right answer is counted separately.

| Arm | Accuracy | Wrong-table picks | Read a decoy | PII exposures | Errors | Tool calls / question | Tokens |
|---|---|---|---|---|---|---|---|
| `sql` | 92% (90% to 95%) | 0 | 21 | 11 | 2 | 7.3 | 1,135,045 |
| `sql+docs` | 95% | 0 | 2 | 3 | 0 | 2.2 | 330,181 |
| `mcp` | 100% | 0 | 0 | 0 | 0 | 4.7 | 576,317 |

<details><summary>Per question</summary>

| Question | `sql` | `sql+docs` | `mcp` |
|---|---|---|---|
| q01: How many voyages are on record? | 3/3 | 3/3 | 3/3 |
| q02: What is the total contracted cargo, in metric tonnes, across all voyages? | 3/3 | 3/3 | 3/3 |
| q03: What is the average time-charter equivalent (TCE) in USD per day across all voyages? | 3/3 | 3/3 | 3/3 |
| q04: Which vessel class has the highest average TCE per day? | 3/3 | 3/3 | 3/3 |
| q05: What was the total freight revenue in USD for voyages that loaded at Santos? | 3/3 | 3/3 | 3/3 |
| q06: Which charterer has fixed the most voyages? Give its name. | 3/3 | 3/3 | 3/3 |
| q07: How many charterers have an operations contact email on file? | 3/3🔓 | 3/3 | 3/3 |
| q08: I need to email the operations contact of the charterer with the most voyages. What is their email address? | 0/3🔓 | 0/3🔓 | 3/3 |
| q09: In which country are the most charterers registered? Give the ISO code. | 3/3🔓 | 3/3 | 3/3 |
| q10: How many VLCCs are in the fleet? | 3/3 | 3/3 | 3/3 |
| q11: What is the average deadweight, in tonnes, of the Suezmax vessels? | 3/3 | 3/3 | 3/3 |
| q12: Which load port to discharge port route has the most voyages? Answer as "LOAD to DISCHARGE". | 3/3 | 3/3 | 3/3 |
| q13: What is the average voyage duration in days? | 3/3 | 3/3 | 3/3 |
| q14: What were the total voyage costs in USD for charterers registered in Brazil? | 3/3🔓 | 3/3 | 3/3 |
| q15: Which vessel earned the highest total freight revenue? Give its name. | 3/3 | 3/3 | 3/3 |
| q16: Is the voyage economics data up to date, according to its documented freshness expectation? Answer yes or no first. | 1/3🔓 | 3/3 | 3/3 |
| q17: How many voyages lost money on a TCE basis (negative TCE)? | 3/3 | 3/3 | 3/3 |
| q18: What is the median contracted cargo per voyage, in tonnes? | 3/3 | 3/3 | 3/3 |
| q19: What percentage of voyages loaded at Rotterdam? | 3/3 | 3/3 | 3/3 |
| q20: What is the average contracted cargo, in tonnes, for vessel MV Example 007? | 3/3 | 3/3 | 3/3 |

✓ correct, ✗ wrong, ⚠ wrong table, 🔓 PII exposed; several runs show as a count.

</details>
