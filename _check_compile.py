from pathlib import Path
p=Path('main.py')
s=p.read_text(encoding='utf-8')
try:
    compile(s, 'main.py', 'exec')
    out='OK\n'
except SyntaxError as e:
    out=f'{e.__class__.__name__}: {e.msg}\nline={e.lineno} offset={e.offset}\ntext={e.text!r}\n'
except Exception as e:
    out=f'{e.__class__.__name__}: {e}\n'
Path('compile_err.txt').write_text(out, encoding='utf-8')
