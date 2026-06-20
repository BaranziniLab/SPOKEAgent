import json, sys, glob
path = sys.argv[1]
for line in open(path):
    r = json.loads(line)
    print("="*80)
    print(f"{r['qid']} [{r.get('tests','')}]  {r['elapsed_s']}s  calls={r['n_tool_calls']}  rc={r['returncode']}  timeout={r['timed_out']}")
    print("Q:", r['question'])
    for t in r['tool_calls']:
        if t.get('phase')=='request':
            args=t.get('args',{})
            cy=args.get('cypher_query') or json.dumps(args)
            print("  >>", t.get('name'), "::", (cy[:300] if isinstance(cy,str) else cy))
        else:
            print("  <<", (t.get('text','')[:200]))
    print("ANSWER:", (r['final_text'] or '(none)')[:600].replace('\n',' '))
    if r.get('stderr_tail'): print("STDERR:", r['stderr_tail'][-300:].replace('\n',' '))
