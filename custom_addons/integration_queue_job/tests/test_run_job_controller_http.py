# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).

from unittest import mock

from odoo import SUPERUSER_ID, http
from odoo.tests.common import HttpCase, tagged

from ..controllers.main import RunJobController


@tagged("-at_install", "post_install")
class TestRunJobHttp(HttpCase):
    """End-to-end tests for the ``/queue_job/runjob`` endpoint.

    ``runjob`` is declared ``auth="none"``, so ``_auth_method_none`` pins
    ``transaction.default_env`` to an env with ``uid=None``. The controller
    must additionally repoint ``default_env`` to a superuser env, otherwise
    any flush during ``job.perform()`` that goes through
    ``Transaction.flush() -> default_env.flush_all()`` (every flushing
    ``cr.savepoint()`` and ``cr.commit()``) recomputes stored fields with
    ``self.env.user`` as an empty recordset and ``self.env.company.id`` False.
    See https://github.com/OCA/queue/pull/923
    """

    def _capture_default_env_at_perform(self):
        job_ = self.env["res.partner"].with_delay().create({"name": "test"})
        job_.set_enqueued()
        job_.store()
        self.env.flush_all()

        captured = {}

        def spy(self_, env, job):
            # Replaces the real execution, which commits (forbidden in tests).
            # Nothing touches default_env between the start of runjob and
            # here, so this is what job.perform() would see.
            default_env = env.transaction.default_env
            captured["job_uuid"] = job.uuid
            captured["uid"] = default_env.uid
            captured["company_id"] = default_env.company.id

        with mock.patch.object(RunJobController, "_try_perform_job", spy):
            # Same request shape as jobrunner/runner.py (db via header)
            response = self.url_open(
                f"/queue_job/runjob?job_uuid={job_.uuid}",
                headers={"X-Odoo-Database": self.env.cr.dbname},
            )
            response.raise_for_status()

        self.assertEqual(captured.get("job_uuid"), job_.uuid)
        return captured

    def test_runjob_pins_default_env_to_superuser(self):
        captured = self._capture_default_env_at_perform()
        self.assertEqual(captured["uid"], SUPERUSER_ID)
        self.assertTrue(captured["company_id"])

    def test_runjob_without_update_env_leaves_uid_none(self):
        # Guards the test above: without update_env the default env keeps the
        # uid=None / no-company state of auth="none", which is the behavior of
        # the former ``env = http.request.env(user=SUPERUSER_ID)``.
        def no_op_update_env(self_, user=None, context=None, su=None):
            pass

        with mock.patch.object(http.Request, "update_env", no_op_update_env):
            captured = self._capture_default_env_at_perform()

        self.assertIsNone(captured["uid"])
        self.assertFalse(captured["company_id"])
