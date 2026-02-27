#compdef aily

_aily() {
    local context state line cmd
    local -i i cmd_index arg_index
    local -a cmd_specs
    cmd_specs=(
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
        'bridge:Manage Discord bridge bot'
        'dashboard:Manage web dashboard'
        'help:Show help information'
        'uninstall:Remove aily hooks'
        'version:Show version'
    )

    _aily_sessions() {
        local -a sessions
        sessions=()

        # Delegate to `aily sessions --json` to respect configured dashboard/auth/fallback logic.
        if (( $+commands[aily] && $+commands[jq] )); then
            sessions=(${(f)"$(aily sessions --json 2>/dev/null | jq -r '(if type == "array" then . else .sessions // [] end) | .[] | .name' 2>/dev/null)"})
        fi

        if (( ${#sessions[@]} == 0 )) && (( $+commands[tmux] )); then
            sessions=(${(f)"$(tmux list-sessions -F '#{session_name}' 2>/dev/null)"})
        fi

        print -l -- $sessions
    }

    _arguments -C \
        '--json[Output in JSON format]' \
        '--verbose[Enable verbose output]' \
        '--help[Show help]' \
        '1:command:->cmd' \
        '*::arg:->args'

    case "$state" in
        cmd)
            _describe -t commands 'aily commands' cmd_specs
            ;;
        args)
            cmd=""
            cmd_index=0
            for (( i = 2; i < CURRENT; i++ )); do
                case "${words[i]}" in
                    --json|--verbose|--help) continue ;;
                    -*) continue ;;
                    *)
                        cmd="${words[i]}"
                        cmd_index=$i
                        break
                        ;;
                esac
            done

            if [[ -z "$cmd" ]]; then
                _describe -t commands 'aily commands' cmd_specs
                return
            fi

            arg_index=$((CURRENT - cmd_index))
            case "$cmd" in
                logs|tail)
                    _arguments \
                        '--json[JSON output]' \
                        '(-n --limit)'{-n,--limit}'[message limit]:count:'
                    if (( arg_index == 1 )); then
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
                    if (( arg_index == 1 )); then
                        local -a sessions
                        sessions=(${(f)"$(_aily_sessions)"})
                        _describe 'session' sessions
                    elif (( arg_index == 2 )); then
                        _values 'format' json markdown
                    fi
                    ;;
                config)
                    if (( arg_index == 1 )); then
                        _values 'config command' show set dashboard-url
                    fi
                    ;;
                auto)
                    if (( arg_index == 1 )); then
                        _values 'mode' on off
                    fi
                    ;;
                bridge)
                    if (( arg_index == 1 )); then
                        _values 'action' start stop restart status logs
                    fi
                    ;;
                dashboard)
                    if (( arg_index == 1 )); then
                        _values 'action' start stop restart status logs
                    fi
                    ;;
            esac
            ;;
    esac
}
