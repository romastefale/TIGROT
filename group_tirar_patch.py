from pathlib import Path

path = Path('main.py')
text = path.read_text(encoding='utf-8')

old = '    app.add_handler(CommandHandler("tirar", tirar))'
new = '    app.add_handler(CommandHandler("tirar", tirar, filters=filters.ChatType.GROUPS | filters.ChatType.PRIVATE))'
if old not in text:
    raise SystemExit('expected CommandHandler line not found')
text = text.replace(old, new)
path.write_text(text, encoding='utf-8')
