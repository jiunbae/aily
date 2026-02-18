# Bash completion for aily CLI
_aily_completions() {
    local cur prev commands
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"

    commands="init status sessions sync logs config doctor send attach help uninstall version"

    case "$prev" in
        aily)
            COMPREPLY=($(compgen -W "$commands" -- "$cur"))
            return 0
            ;;
        sessions)
            COMPREPLY=($(compgen -W "--json --status --host --agent" -- "$cur"))
            return 0
            ;;
        logs|sync|send|attach|export)
            # Complete with session names from dashboard API
            if command -v curl &>/dev/null; then
                local sessions
                sessions=$(curl -sf "http://localhost:8080/api/sessions?limit=50" 2>/dev/null | python3 -c "
import json,sys
try:
    data=json.load(sys.stdin)
    for s in data.get('sessions',[]):
        print(s['name'])
except: pass
" 2>/dev/null)
                COMPREPLY=($(compgen -W "$sessions" -- "$cur"))
            fi
            return 0
            ;;
        config)
            COMPREPLY=($(compgen -W "get set list" -- "$cur"))
            return 0
            ;;
        --status)
            COMPREPLY=($(compgen -W "active idle closed" -- "$cur"))
            return 0
            ;;
        --agent)
            COMPREPLY=($(compgen -W "claude codex gemini opencode" -- "$cur"))
            return 0
            ;;
    esac

    # Global flags
    if [[ "$cur" == -* ]]; then
        COMPREPLY=($(compgen -W "--json --help --verbose" -- "$cur"))
        return 0
    fi
}

complete -F _aily_completions aily
