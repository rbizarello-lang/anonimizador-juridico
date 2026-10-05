from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from anonimizador_app.desktop import (
    STALE_AFTER_SECONDS,
    _older_copies,
    _our_app_pids,
    _parse_pid_lines,
    _process_age,
    format_occupant,
    hidden_run_kwargs,
    holder_is_stale,
    is_our_command_line,
    is_stray_http_server,
    is_worker_process,
    launch_plan,
    merge_pids,
)
from anonimizador_app.branding import ensure_icon, ico_image_count
from anonimizador_app.settings import AppSettings
from anonimizador_app.ui import Session, _commit_target_lists


class DesktopGuardTests(unittest.TestCase):
    def test_recognizes_this_app_only(self) -> None:
        root = Path(r"C:\Users\User\Documents\Anonimizador_Juridico")
        ours = (
            r'"C:\Users\User\Documents\Anonimizador_Juridico\.venv\Scripts\pythonw.exe" '
            r'"C:\Users\User\Documents\Anonimizador_Juridico\app.py"'
        )
        other = r'"C:\Python\python.exe" -m http.server 8770'
        self.assertTrue(is_our_command_line(ours, root))
        self.assertFalse(is_our_command_line(other, root))
        self.assertFalse(is_our_command_line("", root))
        self.assertFalse(
            is_our_command_line(
                r'"C:\Users\User\AppData\Local\Python\pythoncore-3.14-64\python.exe" -m http.server 8765',
                root,
            )
        )

    def test_formats_port_occupant_for_the_dialog(self) -> None:
        root = Path(r"C:\Users\User\Documents\Anonimizador_Juridico")
        ours = (
            r'"C:\Users\User\Documents\Anonimizador_Juridico\.venv\Scripts\pythonw.exe" '
            r'"C:\Users\User\Documents\Anonimizador_Juridico\app.py"'
        )
        foreign = (
            r'"C:\Users\User\AppData\Local\Python\pythoncore-3.14-64\python.exe" '
            r"-m http.server 8765"
        )
        self.assertEqual(format_occupant(10, ours, root), "Anonimizador  (PID 10)")
        self.assertIn("http.server 8765", format_occupant(8924, foreign, root))
        self.assertIn("8924", format_occupant(8924, foreign, root))
        self.assertEqual(_parse_pid_lines("8924\n8924\n16332\n"), [8924, 16332])
        self.assertEqual(merge_pids([17316], [17316, 32424, 18780], [4392]), [17316, 32424, 18780, 4392])
        self.assertTrue(
            is_stray_http_server(
                r'"C:\Users\User\AppData\Local\Python\pythoncore-3.14-64\python.exe" '
                r"-m http.server 8765"
            )
        )
        self.assertFalse(is_stray_http_server(ours))

    def test_launch_plan_focuses_existing_window_instead_of_prompting(self) -> None:
        self.assertEqual(
            launch_plan(
                worker=False,
                has_window=True,
                port_busy=True,
                owns_mutex=False,
                force=False,
            ),
            "focus",
        )
        self.assertEqual(
            launch_plan(
                worker=False,
                has_window=True,
                port_busy=False,
                owns_mutex=True,
                force=False,
            ),
            "reclaim",
        )
        self.assertEqual(
            launch_plan(
                worker=False,
                has_window=False,
                port_busy=False,
                owns_mutex=False,
                force=False,
            ),
            "wait_focus",
        )
        self.assertEqual(
            launch_plan(
                worker=False,
                has_window=False,
                port_busy=True,
                owns_mutex=True,
                force=False,
            ),
            "reclaim",
        )
        self.assertEqual(
            launch_plan(
                worker=True,
                has_window=False,
                port_busy=True,
                owns_mutex=False,
                force=True,
            ),
            "continue",
        )
        self.assertEqual(
            launch_plan(
                worker=False,
                has_window=False,
                port_busy=False,
                owns_mutex=True,
                force=False,
            ),
            "start",
        )

    def test_stuck_copy_is_replaced_instead_of_waiting_forever(self) -> None:
        # 26/09/2026: a copia fechada ficou viva sem porta, com a trava; cada clique esperava 2 min
        # e desistia com "ainda esta abrindo".
        stuck = dict(worker=False, has_window=False, port_busy=False, owns_mutex=False, force=False)
        self.assertEqual(launch_plan(**stuck, stale=True), "reclaim")
        self.assertEqual(launch_plan(**stuck, stale=False), "wait_focus")
        # Porta servida (ex.: modo navegador, sem janela nativa): esta viva, nunca e derrubada.
        self.assertEqual(launch_plan(**{**stuck, "port_busy": True}, stale=True), "wait_focus")
        self.assertTrue(holder_is_stale([12.0, STALE_AFTER_SECONDS + 1]))
        self.assertFalse(holder_is_stale([12.0, 40.0]))
        self.assertFalse(holder_is_stale([]))

    @unittest.skipUnless(os.name == "nt", "idade de processo pelo Windows")
    def test_reclaim_only_takes_down_older_copies(self) -> None:
        root = Path(__file__).resolve().parents[1]
        own_age = _process_age(os.getpid())
        self.assertIsNotNone(own_age)
        self.assertIsNone(_process_age(0))
        # Copia "mais nova" deste app.py: outro clique, que decide sozinho e nao pode ser derrubado.
        younger = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)", str(root / "app.py")],
            **hidden_run_kwargs(),
        )
        try:
            deadline = time.monotonic() + 15
            while younger.pid not in _our_app_pids(root) and time.monotonic() < deadline:
                time.sleep(0.2)
            self.assertIn(younger.pid, _our_app_pids(root))
            age = _process_age(younger.pid)
            self.assertIsNotNone(age)
            self.assertLess(age, _process_age(os.getpid()))
            self.assertNotIn(younger.pid, _older_copies(root))
        finally:
            younger.kill()
            younger.wait(timeout=10)

    def test_worker_detection_and_hidden_subprocess_flags(self) -> None:
        self.assertFalse(is_worker_process())
        flags = hidden_run_kwargs()
        self.assertIn("creationflags", flags)
        self.assertEqual(flags["creationflags"], 0x08000000)
        self.assertTrue(flags["startupinfo"].dwFlags & 0x00000001)

    def test_icon_file_has_multiple_sizes(self) -> None:
        # A arte do proprio repositorio, gerada numa pasta temporaria (sem depender da maquina).
        art = Path(__file__).resolve().parents[1] / "recursos" / "anonimizador.png"
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            (root / "recursos").mkdir()
            shutil.copyfile(art, root / "recursos" / "anonimizador.png")
            ico = ensure_icon(root)
            self.assertIsNotNone(ico)
            assert ico is not None
            self.assertGreater(ico.stat().st_size, 20_000)
            self.assertEqual(ico_image_count(ico), 7)


class TargetListSessionTests(unittest.TestCase):
    def test_both_views_read_the_same_session_lists(self) -> None:
        session = Session.__new__(Session)
        session.settings = AppSettings()
        session.target_companies_text = ""
        session.target_people_text = ""
        session.target_ids_text = ""

        _commit_target_lists(session, "Alfa Ltda\n", "Joao Silva")
        view_anonimizar = (
            session.target_companies_text,
            session.target_people_text,
            session.target_ids_text,
        )
        view_regras = (
            session.target_companies_text,
            session.target_people_text,
            session.target_ids_text,
        )
        self.assertEqual(view_anonimizar, view_regras)
        self.assertEqual(session.settings.target_companies, ["Alfa Ltda"])
        self.assertEqual(session.settings.target_people, ["Joao Silva"])
        self.assertEqual(session.settings.target_ids, [])

        _commit_target_lists(
            session,
            "Alfa Ltda\nBeta SA",
            "Joao Silva",
            "52998224725\n00.394.460/0216-53\n12.345.678/0001-95",
        )
        self.assertEqual(session.target_companies_text, "Alfa Ltda\nBeta SA")
        self.assertEqual(session.settings.target_companies, ["Alfa Ltda", "Beta SA"])
        self.assertEqual(session.target_people_text, "Joao Silva")
        self.assertEqual(
            session.settings.target_ids,
            ["529.982.247-25", "12.345.678/0001-95"],
        )
        self.assertEqual(
            session.target_ids_text,
            "529.982.247-25\n12.345.678/0001-95",
        )


if __name__ == "__main__":
    unittest.main()
