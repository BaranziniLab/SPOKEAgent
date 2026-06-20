import json, sys
# summarize a results jsonl: per-question efficiency + whether resolve_entity was used
path=sys.argv[1]
recs=[json.loads(l) for l in open(path)]
tot_t=tot_c=0
print(f"{'QID':5} {'s':>6} {'calls':>5} {'resolve':>7} {'schema':>6} {'rc':>3}  answer_head")
for r in recs:
    reqs=[t for t in r['tool_calls'] if t.get('phase')=='request']
    names=[(t.get('name') or '') for t in reqs]
    nres=sum('resolve_entity' in n for n in names)
    nsch=sum('get_spoke_schema' in n for n in names)
    tot_t+=r['elapsed_s']; tot_c+=r['n_tool_calls']
    ans=(r['final_text'] or '').replace(chr(10),' ')[:60]
    print(f"{r['qid']:5} {r['elapsed_s']:6.1f} {r['n_tool_calls']:5} {nres:7} {nsch:6} {r['returncode']:3}  {ans}")
n=len(recs)
print(f"\nMEAN: {tot_t/n:.1f}s  {tot_c/n:.1f} calls/q   (n={n})")
