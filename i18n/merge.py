import re,json,glob,sys
from pathlib import Path
D=Path(__file__).parent
def parse(t):
    out={}
    for m in re.finditer(r'^@@(\d+)\n(.*?)(?=^@@\d+\n|^#### |\Z)',t,re.S|re.M):
        out[int(m.group(1))]=m.group(2).strip('\n')
    return out
todo=parse((D/'todo-uk.txt').read_text())
for lang in ['uk','en']:
    f=D/f'{lang}.json'; d=json.loads(f.read_text()) if f.exists() else {}
    n=0
    for w in sorted(glob.glob(str(D/'work'/f'{lang}-*.txt'))):
        for k,v in parse(Path(w).read_text()).items():
            ru=todo[k]; lead=ru[:len(ru)-len(ru.lstrip(' '))]; trail=ru[len(ru.rstrip(' ')):]
            d[ru]=lead+v.strip(' ')+trail if (lead or trail) else v; n+=1
    f.write_text(json.dumps(d,ensure_ascii=False,indent=0),encoding='utf-8')
    print(lang,n,'переводов, в словаре',len(d))
