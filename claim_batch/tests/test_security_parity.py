"""
Security parity of batch runs and relative indexes: rows, summaries and the batch
processing stay within the user's locations (BatchRun / RelativeIndex.row_scope =
LocationScope("location"), a null location being national and visible).

Fixture: core.test_helpers.create_security_tree -> R1>D1 / R2>D2; a batch run and a
relative index in D1, in D2 and nationally; `user_a` holds the batch rights globally and
is assigned district D1 only (so region R1, whose only district it is, too).
"""
import datetime
import json

from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings

from claim_batch.apps import ClaimBatchConfig
from claim_batch.models import BatchRun, RelativeIndex
from core.models.openimis_graphql_test_case import openIMISGraphQLTestCase
from core.test_helpers import (create_security_tree, create_right_only_user, get_test_token, perm_ids,
                               create_test_interactive_user)

YEAR = 2003
RUN_DATE = "2003-01-31T00:00:00"


def _u(*lists):
    return list(dict.fromkeys(r for lst in lists for r in lst))


def _product(code):
    """product.test_helpers imports program.test_helpers, absent on Cameroon's program."""
    from program.models import Program
    from product.models import Product
    program = Program.objects.create(code=code[:10], nameProgram=f"Program {code}")
    return Product.objects.create(
        code=code, name=f"Test product {code}", lump_sum=100, max_members=5,
        grace_period_enrolment=1, insurance_period=12, date_from="2019-01-01", date_to="2049-01-01",
        validity_from="2019-01-01", audit_user_id=-1, program=program)


BATCH_RIGHTS = _u(perm_ids(ClaimBatchConfig.gql_query_batch_runs_perms),
                  perm_ids(ClaimBatchConfig.gql_mutation_process_batch_perms))


def _build(cls, prefix):
    cls.t = create_security_tree(prefix)
    cls.product = _product(f"{prefix}PR")
    now = datetime.datetime(YEAR, 1, 31)
    for key, location, month in (("d1", cls.t.d1, 1), ("d2", cls.t.d2, 2), ("nat", None, 3),
                                 ("r2", cls.t.r2, 4)):
        run = BatchRun.objects.create(location=location, run_date=now, audit_user_id=-1, run_year=YEAR,
                                      run_month=month, validity_from=now)
        index = RelativeIndex.objects.create(product=cls.product, type=RelativeIndex.TYPE_MONTH,
                                             care_type=RelativeIndex.CARE_TYPE_BOTH, year=YEAR, period=month,
                                             calc_date=now, rel_index=month, audit_user_id=-1,
                                             location=location, validity_from=now)
        setattr(cls, f"run_{key}", run)
        setattr(cls, f"idx_{key}", index)
    cls.user_a = create_right_only_user(f"{prefix.lower()}user", BATCH_RIGHTS, [cls.t.d1.id])
    cls.user_norights = create_right_only_user(f"{prefix.lower()}none", [], [cls.t.d1.id])
    cls.admin = create_test_interactive_user(username=f"{prefix.lower()}admin")


@override_settings(ROW_SECURITY=True)
class BatchRunReadScopeTest(openIMISGraphQLTestCase):

    @classmethod
    def setUpTestData(cls):
        _build(cls, "CBR")
        cls.token = get_test_token(cls.user_a)
        cls.token_norights = get_test_token(cls.user_norights)

    def setUp(self):
        cache.clear()

    def _q(self, query, token=None):
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
        return json.loads(self.query(query, headers=headers).content)

    def _run_months(self, content):
        return {e["node"]["runMonth"] for e in content["data"]["batchRuns"]["edges"]}

    def test_model_scope(self):
        ids = set(BatchRun.get_queryset(BatchRun.objects.all(), self.user_a).values_list("id", flat=True))
        self.assertTrue({self.run_d1.id, self.run_nat.id} <= ids)
        self.assertFalse({self.run_d2.id, self.run_r2.id} & ids)
        ids = set(RelativeIndex.get_queryset(None, self.user_a).values_list("id", flat=True))
        self.assertTrue({self.idx_d1.id, self.idx_nat.id} <= ids)
        self.assertFalse({self.idx_d2.id, self.idx_r2.id} & ids)
        self.assertFalse(BatchRun.get_queryset(None, None).exists())

    def test_batch_runs_listed_within_the_districts(self):
        content = self._q(f'{{ batchRuns(runDate: "{RUN_DATE}") {{ edges {{ node {{ runMonth }} }} }} }}', self.token)
        self.assertNotIn("errors", content, content.get("errors"))
        self.assertEqual({1, 3}, self._run_months(content))

    def test_deleted_batch_runs_only_on_request(self):
        BatchRun.objects.filter(id=self.run_d1.id).update(validity_to=datetime.datetime(YEAR, 2, 1))
        content = self._q(f'{{ batchRuns(runDate: "{RUN_DATE}") {{ edges {{ node {{ runMonth }} }} }} }}', self.token)
        self.assertEqual({3}, self._run_months(content))
        deleted = self._q(f'{{ batchRuns(runDate: "{RUN_DATE}", isDeleted: true) {{ edges {{ node {{ runMonth }} }} }} }}',
                          self.token)
        self.assertEqual({1}, self._run_months(deleted))

    def test_batch_run_node_out_of_scope_not_found(self):
        from graphql_relay import to_global_id
        node_id = to_global_id("BatchRunGQLType", self.run_d2.id)
        content = self._q(f'{{ node(id: "{node_id}") {{ ... on BatchRunGQLType {{ runMonth }} }} }}', self.token)
        self.assertIsNone((content.get("data") or {}).get("node"), content)

    def test_relative_indexes_need_a_right(self):
        query = '{ relativeIndexes { edges { node { id } } } }'
        self.assertTrue(self._q(query).get("errors"), "anonymous")
        self.assertTrue(self._q(query, self.token_norights).get("errors"), "no right")

    def test_relative_indexes_within_the_districts(self):
        content = self._q(f'{{ relativeIndexes(product_Code: "{self.product.code}") '
                          f'{{ edges {{ node {{ period }} }} }} }}', self.token)
        self.assertNotIn("errors", content, content.get("errors"))
        self.assertEqual({1, 3}, {e["node"]["period"] for e in content["data"]["relativeIndexes"]["edges"]})

    def test_summaries_of_another_district_refused(self):
        query = '{ batchRunsSummaries(accountYear: %d, accountDistrict: %d) { edges { node { index } } } }'
        own = self._q(query % (YEAR, self.t.d1.id), self.token)
        self.assertNotIn("errors", own, own.get("errors"))
        self.assertEqual([1.0], [e["node"]["index"] for e in own["data"]["batchRunsSummaries"]["edges"]])
        other = self._q(query % (YEAR, self.t.d2.id), self.token)
        self.assertEqual([], (other.get("data") or {}).get("batchRunsSummaries", {}).get("edges", []), other)
        region = self._q('{ batchRunsSummaries(accountYear: %d, accountRegion: %d) { edges { node { index } } } }'
                         % (YEAR, self.t.r2.id), self.token)
        self.assertEqual([], (region.get("data") or {}).get("batchRunsSummaries", {}).get("edges", []), region)


@override_settings(ROW_SECURITY=True)
class ProcessBatchScopeTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        _build(cls, "CBP")

    def setUp(self):
        cache.clear()

    def _process(self, user, location_id, month):
        from claim_batch.schema import ProcessBatchMutation
        data = {"year": YEAR + 1, "month": month}
        if location_id is not ...:
            data["location_id"] = location_id
        return ProcessBatchMutation.async_mutate(user, **data)

    def _runs(self, location):
        # do_process_batch files a run under the month after the one asked (its end date)
        return BatchRun.objects.filter(run_year=YEAR + 1, location=location)

    def test_batch_of_another_district_refused(self):
        with self.assertRaises(PermissionDenied):
            self._process(self.user_a, self.t.d2.id, 1)
        self.assertFalse(self._runs(self.t.d2).exists())

    def test_batch_of_a_region_not_fully_assigned_refused(self):
        with self.assertRaises(PermissionDenied):
            self._process(self.user_a, self.t.r2.id, 2)
        self.assertFalse(self._runs(self.t.r2).exists())

    def test_national_batch_refused_to_a_district_user(self):
        for month, location_id in ((3, None), (4, -1), (5, ...)):
            with self.assertRaises(PermissionDenied):
                self._process(self.user_a, location_id, month)
            self.assertFalse(self._runs(None).exists(), location_id)

    def test_village_or_unknown_location_refused(self):
        with self.assertRaises(PermissionDenied):
            self._process(self.user_a, self.t.v1.id, 6)
        with self.assertRaises(PermissionDenied):
            self._process(self.user_a, 999999999, 7)
        self.assertFalse(self._runs(self.t.v1).exists())

    def test_batch_of_own_district_allowed(self):
        self.assertFalse(self._process(self.user_a, self.t.d1.id, 8))
        self.assertTrue(self._runs(self.t.d1).exists())

    def test_location_scope_helper(self):
        from claim_batch.services import batch_location_allowed
        self.assertTrue(batch_location_allowed(self.user_a, self.t.d1.id))
        self.assertTrue(batch_location_allowed(self.user_a, self.t.r1.id))
        self.assertFalse(batch_location_allowed(self.user_a, self.t.d2.id))
        self.assertFalse(batch_location_allowed(self.user_a, None))
        # an imis administrator is not location restricted: national and any district
        self.assertTrue(batch_location_allowed(self.admin, None))
        self.assertTrue(batch_location_allowed(self.admin, self.t.d2.id))
        with override_settings(ROW_SECURITY=False):
            self.assertTrue(batch_location_allowed(self.user_a, None))
