#compdef aily

_aily_sessions() {
    local -a sessions
    sessions=()

    if (( $+commands[curl] && $+commands[python3] )); then
        sessions=(${(f)"$(curl -sf http://localhost:8080/api/sessions?limit=50 2>/dev/null | python3 -c "
import json,sys
try:
    data=json.load(sys.stdin)
    for s in data.get('sessions',[]):
        print(s['name'])
except: pass
" 2>/dev/null)"})
    fi

    if (( ${#sessions[@]} == 0 )) && (( $+commands[tmux] )); then
        sessions=(${(f)"$(tmux list-sessions -F '#{session_name}' 2>/dev/null)"})
    fi

    print -l -- $sessions
}

_aily() {
    local context state line
    local -a commands
    commands=(
        'init:Set up aily hooks and configuration'
        'status:Show connection and hook status'
        'sessions:List active sessions'
        'sync:Sync messages for a session'
        'logs:Show recent messages for a session'
        'tail:Alias for logs'
        'config:Show/set configuration values'
        'doctor:Run diagnostic checks'
        'attach:Attach to a tmux session'
        'export:Export session messages'
        'start:Create a thread for a session'
        'stop:Archive/delete a thread for a session'
        'auto:Toggle auto thread sync'
        'help:Show help information'
        'uninstall:Remove aily hooks'
        'version:Show version'
    )

    _arguments -C \
        '--json[Output in JSON format]' \
        '--verbose[Enable verbose output]' \
        '--help[Show help]' \
        '1:command:->cmd' \
        '*::arg:->args'

    case "$state" in
        cmd)
            _describe -t commands 'aily commands' commands
            ;;
        args)
            local cmd
            cmd="${words[2]}"
            case "$cmd" in
                logs|tail)
                    _arguments \
                        '--json[JSON output]' \
                        '(-n --limit)'{-n,--limit}'[message limit]:count:'
                    if (( CURRENT == 3 )); then
                        local -a sessions
                        sessions=(${(f)"$(_aily_sessions)"})
                        _describe 'session' sessions
                    fi
                    ;;
                sessions)
                    _arguments \
                        '--ssh[List via SSH instead of dashboard API]' \
                        '--json[JSON output]'
                    ;;
                sync|attach|start|stop)
                    local -a sessions
                    sessions=(${(f)"$(_aily_sessions)"})
                    _describe 'session' sessions
                    ;;
                export)
                    if (( CURRENT == 3 )); then
                        local -a sessions
                        sessions=(${(f)"$(_aily_sessions)"})
                        _describe 'session' sessions
                    elif (( CURRENT == 4 )); then
                        _values 'format' json markdown
                    fi
                    ;;
                config)
                    if (( CURRENT == 3 )); then
                        _values 'config command' show set dashboard-url
                    fi
                    ;;
                auto)
                    _values 'mode' on off
                    ;;
            esac
            ;;
    esac
}
