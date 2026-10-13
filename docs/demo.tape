# Regenerate docs/demo.gif: vhs docs/demo.tape   (run from the repository root)
Output docs/demo.gif
Set Shell bash
Set FontSize 14
Set Width 1380
Set Height 760
Set Padding 18
Set Theme "Builtin Dark"
Set TypingSpeed 45ms

Hide
Type "export PS1='$ ' && uv run tracecost --version >/dev/null && clear"
Enter
Sleep 2s
Show

Type "uv run tracecost cost examples/demo/traces.jsonl --by trace"
Sleep 400ms
Enter
Sleep 7s
Hide
Type "clear"
Enter
Show
Type "uv run tracecost cost examples/demo/traces.jsonl --by user"
Sleep 400ms
Enter
Sleep 6s
