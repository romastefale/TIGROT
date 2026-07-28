from typing import Any, Callable, Dict, List
import threading

from android_utils import run_on_ui_thread
from base_plugin import BasePlugin, MenuItemData, MenuItemType
from client_utils import get_user_config, send_request, send_text
from ui.bulletin import BulletinHelper

from org.telegram.tgnet import TLRPC


__id__ = "mtproto_rpc_diagnostics_jonas"
__name__ = "MTProto RPC Diagnostics"
__description__ = (
    "Executa chamadas RPC tipadas e somente de leitura usando a infraestrutura "
    "normal do cliente Telegram. Não envia pacotes brutos arbitrários."
)
__author__ = "Jonas"
__version__ = "1.0.1"
__icon__ = "msg_info"
__app_version__ = ">=12.8.1"
__sdk_version__ = ">=1.4.3.10"


def _java_list(value: Any) -> List[Any]:
    """Converte uma lista ou coleção Java em uma lista Python."""
    if value is None:
        return []

    try:
        return list(value)
    except (TypeError, ValueError):
        pass

    try:
        return [value.get(i) for i in range(value.size())]
    except Exception:
        return []


def _safe_attr(obj: Any, name: str, default: Any = None) -> Any:
    """Lê um atributo sem interromper o diagnóstico em caso de erro Java/Python."""
    try:
        return getattr(obj, name, default)
    except Exception:
        return default


def _format_rpc_error(error: Any) -> str:
    if error is None:
        return "resposta vazia"

    text = _safe_attr(error, "text", None)
    code = _safe_attr(error, "code", None)

    if text and code is not None:
        return f"{code}: {text}"
    if text:
        return str(text)
    return str(error)


class MTProtoRpcDiagnosticsPlugin(BasePlugin):
    """
    Diagnóstico seguro do MTProto usando o sistema normal de requests.

    Chamadas realizadas:
      - help.getConfig
      - users.getUsers(inputUserSelf)
    """

    def on_plugin_load(self) -> None:
        self.add_menu_item(
            MenuItemData(
                menu_type=MenuItemType.CHAT_ACTION_MENU,
                text="Executar diagnóstico MTProto",
                on_click=self.on_menu_click,
                icon="msg_info",
                subtext="Consulta configuração e usuário atual",
                priority=5,
            )
        )
        self.log("MTProto RPC Diagnostics carregado")

    def on_plugin_unload(self) -> None:
        self.log("MTProto RPC Diagnostics descarregado")

    def on_menu_click(self, context: Dict[str, Any]) -> None:
        run_on_ui_thread(
            lambda: BulletinHelper.show_info("Executando consultas RPC...")
        )
        self._run_diagnostics()

    def _run_diagnostics(self) -> None:
        state: Dict[str, Any] = {
            "pending": 2,
            "lines": [
                "MTProto RPC Diagnostics",
                "",
            ],
        }
        state_lock = threading.Lock()

        def append_line(line: str) -> None:
            with state_lock:
                state["lines"].append(line)

        def append_lines(lines: List[str]) -> None:
            with state_lock:
                state["lines"].extend(lines)

        def finish_one() -> None:
            with state_lock:
                state["pending"] -= 1
                if state["pending"] > 0:
                    return
                report = "\n".join(state["lines"])

            try:
                saved_messages_id = get_user_config().getClientUserId()
                send_text(saved_messages_id, report)
                run_on_ui_thread(
                    lambda: BulletinHelper.show_success(
                        "Diagnóstico enviado para Mensagens Salvas."
                    )
                )
            except Exception as exc:
                self.log(f"Falha ao enviar relatório: {exc}")
                run_on_ui_thread(
                    lambda: BulletinHelper.show_error(
                        "O diagnóstico terminou, mas o relatório não pôde ser enviado."
                    )
                )

        self._request_config(append_line, append_lines, finish_one)
        self._request_current_user(append_line, append_lines, finish_one)

    def _request_config(
        self,
        append_line: Callable[[str], None],
        append_lines: Callable[[List[str]], None],
        done: Callable[[], None],
    ) -> None:
        request = TLRPC.TL_help_getConfig()

        def callback(response: Any, error: Any) -> None:
            try:
                if error is not None or response is None:
                    append_line(
                        f"help.getConfig: erro: {_format_rpc_error(error)}"
                    )
                    return

                append_lines(
                    [
                        "help.getConfig: OK",
                        f"  this_dc: {_safe_attr(response, 'this_dc', '?')}",
                        f"  dc_options: {len(_java_list(_safe_attr(response, 'dc_options')))}",
                        f"  chat_size_max: {_safe_attr(response, 'chat_size_max', '?')}",
                        f"  megagroup_size_max: {_safe_attr(response, 'megagroup_size_max', '?')}",
                        "",
                    ]
                )
            except Exception as exc:
                append_line(f"help.getConfig: falha ao interpretar: {exc}")
            finally:
                done()

        try:
            send_request(request, callback)
        except Exception as exc:
            append_line(f"help.getConfig: falha ao enviar: {exc}")
            done()

    def _request_current_user(
        self,
        append_line: Callable[[str], None],
        append_lines: Callable[[List[str]], None],
        done: Callable[[], None],
    ) -> None:
        request = TLRPC.TL_users_getUsers()
        request.id = [TLRPC.TL_inputUserSelf()]

        def callback(response: Any, error: Any) -> None:
            try:
                if error is not None or response is None:
                    append_line(
                        f"users.getUsers(self): erro: {_format_rpc_error(error)}"
                    )
                    return

                users = _java_list(response)
                if not users:
                    append_line("users.getUsers(self): resposta sem usuários")
                    return

                user = users[0]
                append_lines(
                    [
                        "users.getUsers(self): OK",
                        f"  id: {_safe_attr(user, 'id', '?')}",
                        f"  first_name: {_safe_attr(user, 'first_name', '')}",
                        f"  last_name: {_safe_attr(user, 'last_name', '')}",
                        f"  username: {_safe_attr(user, 'username', '')}",
                        f"  premium: {bool(_safe_attr(user, 'premium', False))}",
                    ]
                )
            except Exception as exc:
                append_line(f"users.getUsers(self): falha ao interpretar: {exc}")
            finally:
                done()

        try:
            send_request(request, callback)
        except Exception as exc:
            append_line(f"users.getUsers(self): falha ao enviar: {exc}")
            done()
          
