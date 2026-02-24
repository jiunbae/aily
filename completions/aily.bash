# Bash completion for aily CLI
_aily_completions() {
    local cur prev cmd commands
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]:-}"
    cmd="${COMP_WORDS[1]:-}"

    commands="init status sessions sync logs tail attach export config doctor start stop auto uninstall help version"

    _aily_fetch_sessions() {
        if command -v curl >/dev/null 2>&1 && command -v python3 >/dev/null 2>&1; then
            curl -sf "http://localhost:8080/api/sessions?limit=50" 2>/dev/null | python3 -c "
import json,sys
try:
    data=json.load(sys.stdin)
    for s in data.get('sessions',[]):
        print(s['name'])
except: pass"
            return
        fi
        if command -v tmux >/dev/null 2>&1; then
            tmux list-sessions -F '#{session_name}' 2>/dev/null || true
        fi
    }

    if [[ "$COMP_CWORD" -eq 1 ]]; then
        COMPREPLY=($(compgen -W "$commands" -- "$cur"))
        return 0
    fi

    case "$cmd" in
        sessions)
            COMPREPLY=($(compgen -W "--json --ssh" -- "$cur"))
            return 0
            ;;
        logs|tail)
            if [[ "$prev" == "--limit" || "$prev" == "-n" ]]; then
                return 0
            fi
            local sessions
            sessions="$(_aily_fetch_sessions)"
            COMPREPLY=($(compgen -W "--json --limit -n $sessions" -- "$cur"))
            return 0
            ;;
        sync|attach|start|stop)
            local sessions
            sessions="$(_aily_fetch_sessions)"
            COMPREPLY=($(compgen -W "$sessions" -- "$cur"))
            return 0
            ;;
        export)
            if [[ "$COMP_CWORD" -eq 2 ]]; then
                local sessions
                sessions="$(_aily_fetch_sessions)"
                COMPREPLY=($(compgen -W "$sessions" -- "$cur"))
            elif [[ "$COMP_CWORD" -eq 3 ]]; then
                COMPREPLY=($(compgen -W "json markdown" -- "$cur"))
            fi
            return 0
            ;;
        config)
            if [[ "$COMP_CWORD" -eq 2 ]]; then
                COMPREPLY=($(compgen -W "show set dashboard-url" -- "$cur"))
            fi
            return 0
            ;;
        auto)
            COMPREPLY=($(compgen -W "on off" -- "$cur"))
            return 0
            ;;
    esac

    # Global flags
    if [[ "$COMP_CWORD" -eq 1 && "$cur" == -* ]]; then
        COMPREPLY=($(compgen -W "--json --help --verbose" -- "$cur"))
        return 0
    fi
}

complete -F _aily_completions aily
