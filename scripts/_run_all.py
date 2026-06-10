import subprocess, pathlib, sys, json, argparse, tempfile


def parse_args():
    p = argparse.ArgumentParser(
        description='Run all pr-sheriff checks on a target SQL file.')
    p.add_argument('target', help='Path to the target .sql model file')
    p.add_argument('--root',
                   help='Path to the dbt project root directory (default: auto-discovered from target)')
    p.add_argument('--skill-dir', default=str(pathlib.Path(__file__).resolve().parent),
                   help='Path to the skill scripts directory (default: directory containing this script)')
    p.add_argument('--tmp-dir', default=tempfile.gettempdir(),
                   help='Temporary directory for intermediate files (default: system temp)')
    return p.parse_args()


args = parse_args()
TARGET = args.target
SKILL = args.skill_dir
TMP = args.tmp_dir

def run(label, cmd_args):
    r = subprocess.run([sys.executable] + cmd_args, capture_output=True, text=True)
    if r.returncode != 0:
        print(f'[{label}] FAILED rc={r.returncode}')
        if r.stderr: print(r.stderr)
        sys.exit(r.returncode)
    return r

r1 = run('discover',    [f'{SKILL}/discover_project.py', TARGET])
pathlib.Path(f'{TMP}/discovery.json').write_text(r1.stdout, encoding='utf-8')

discovery = json.loads(r1.stdout)
ROOT = args.root if args.root else discovery['project_root']

r2 = run('parse',       [f'{SKILL}/parse_target.py', TARGET, ROOT])
pathlib.Path(f'{TMP}/parsed.json').write_text(r2.stdout, encoding='utf-8')

r3 = run('check_refs',  [f'{SKILL}/check_refs.py', f'{TMP}/parsed.json', f'{TMP}/discovery.json'])
pathlib.Path(f'{TMP}/refs.json').write_text(r3.stdout, encoding='utf-8')

r4 = run('check_env',   [f'{SKILL}/check_env.py', f'{TMP}/parsed.json', f'{TMP}/refs.json'])

parsed   = json.loads(r2.stdout)
refs     = json.loads(r3.stdout)
env_chk  = json.loads(r4.stdout)

print('=== TARGET ENV-TAGS ===')
print(json.dumps(parsed['env']['env_tags']))

print('\n=== REFS ENV-TAGS ===')
for ref in refs:
    print(f"  {ref['raw']}")
    print(f"    env_tags: {ref['resolved_env']['env_tags']}")

print('\n=== ENV CHECK ===')
for e in env_chk:
    detail = e['detail'].encode('ascii', errors='replace').decode('ascii')
    print(f"  {e['status']}  {e['raw']}")
    print(f"    {detail}")
