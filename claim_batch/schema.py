import graphene
from django.core.exceptions import PermissionDenied
from django.db import connection
from core import prefix_filterset, ExtendedConnection
from core.schema import OpenIMISMutation, OrderedDjangoFilterConnectionField
from core.gql import ScopedQuerysetMixin, legacy_row_filters, row_filter_arguments
from core.models import Validity
from graphene import ObjectType
from graphene_django import DjangoObjectType
from product.schema import ProductGQLType
from location.schema import LocationGQLType
from .models import BatchRun, RelativeIndex
from .services import ProcessBatchSubmit, ProcessBatchService, batch_location_allowed
from .apps import ClaimBatchConfig
from django.utils.translation import gettext as _
from django.conf import settings

class BatchRunGQLType(ScopedQuerysetMixin, DjangoObjectType):
    class Meta:
        model = BatchRun
        interfaces = (graphene.relay.Node,)
        filter_fields = {
            "id": ["exact"],
            "run_date": ["exact", "lt", "lte", "gt", "gte"],
            "location": ["isnull"],
            **prefix_filterset("location__", LocationGQLType._meta.filter_fields),
        }
        connection_class = ExtendedConnection


class BatchRunSummaryGQLType(ObjectType):
    run_year = graphene.Int()
    run_month = graphene.Int()
    product_label = graphene.String()
    care_type = graphene.String()
    calc_date = graphene.String()
    index = graphene.Float()

    class Meta:
        interfaces = (graphene.relay.Node,)


def batchRunSummaryFilter(**kwargs):
    params = []
    filter = ''
    if kwargs.get('accountType'):
        filter += 'r."RelType" = %s AND '
        params.append(kwargs.get('accountType'))
    if kwargs.get('accountYear'):
        filter += 'b."RunYear" = %s AND '
        params.append(kwargs.get('accountYear'))
    if kwargs.get('accountMonth'):
        filter += 'b."RunMonth" = %s AND '
        params.append(kwargs.get('accountMonth'))
    if kwargs.get('accountDistrict'):
        filter += 'l."LocationId" = %s AND '
        params.append(kwargs.get('accountDistrict'))
    elif kwargs.get('accountRegion'):
        filter += 'l."LocationId" = %s AND '
        params.append(kwargs.get('accountRegion'))
    else:
        filter += 'l."LocationId" is NULL AND '
    if kwargs.get('accountProduct'):
        filter += 'r."ProdID" = %s AND '
        params.append(kwargs.get('accountProduct'))
    if kwargs.get('accountCareType'):
        filter += "r.\"RelCareType\" = %s AND "
        params.append(kwargs.get('accountCareType'))
    return filter + '1 = 1', params


class BatchRunSummaryConnection(graphene.Connection):
    class Meta:
        node = BatchRunSummaryGQLType

    total_count = graphene.Int()

    def resolve_total_count(self, info, **kwargs):
        return len(self.iterable)


class RelativeIndexGQLType(ScopedQuerysetMixin, DjangoObjectType):
    class Meta:
        model = RelativeIndex
        interfaces = (graphene.relay.Node,)
        filter_fields = {
            "id": ["exact"],
            "period": ["exact"],
            "care_type": ["exact"],
            **prefix_filterset("product__", ProductGQLType._meta.filter_fields)
        }
        connection_class = ExtendedConnection


class ProcessBatchMutation(OpenIMISMutation):
    """
    Process Batch.
    """
    _mutation_module = "claim_batch"
    _mutation_class = "ProcessBatchMutation"

    class Input(OpenIMISMutation.Input):
        location_id = graphene.Int(required=False)
        year = graphene.Int()
        month = graphene.Int()

    @classmethod
    def async_mutate(cls, user, **data):
        if not user.has_perms(ClaimBatchConfig.gql_mutation_process_batch_perms):
            raise PermissionDenied(_("unauthorized"))
        location_id = data.get('location_id', None)
        if location_id == -1:
            location_id = None
        # the batch valuates the claims and triggers the payment calculation rules of
        # its location: a region / district of the user's, national (null / -1) only
        # for a user who is not location restricted
        if not batch_location_allowed(user, location_id):
            raise PermissionDenied(_("unauthorized"))
        submit = ProcessBatchSubmit(
            location_id=location_id,
            year=data['year'],
            month=data['month']
        )
        service = ProcessBatchService(user)
        res = service.submit(submit)
        return res


class Query(graphene.ObjectType):
    batch_runs = OrderedDjangoFilterConnectionField(
        BatchRunGQLType,
        **row_filter_arguments(),
        orderBy=graphene.List(of_type=graphene.String))
    batch_runs_summaries = graphene.relay.ConnectionField(
        BatchRunSummaryConnection,
        accountType=graphene.Int(),
        accountYear=graphene.Int(),
        accountMonth=graphene.Int(),
        accountRegion=graphene.Int(),
        accountDistrict=graphene.Int(),
        accountProduct=graphene.Int(),
        accountCareType=graphene.String()
    )
    relative_indexes = OrderedDjangoFilterConnectionField(
        RelativeIndexGQLType,
        **row_filter_arguments(),
        orderBy=graphene.List(of_type=graphene.String))

    def resolve_batch_runs(self, info, **kwargs):
        if not info.context.user.has_perms(ClaimBatchConfig.gql_query_batch_runs_perms):
            raise PermissionDenied(_("unauthorized"))
        # the runs of the user's locations (and national ones) by BatchRun.get_queryset
        # (BatchRunGQLType); current ones unless isDeleted / showHistory ask (OP-2029)
        legacy_row_filters(info, kwargs, validity=Validity.NOW)
        return BatchRun.objects.all()

    def resolve_batch_runs_summaries(self, info, **kwargs):
        user = info.context.user
        if not user.has_perms(ClaimBatchConfig.gql_query_batch_runs_perms):
            raise PermissionDenied(_("unauthorized"))
        # the district (or else the region) asked for must be one of the user's; no
        # location means the national indexes, visible to all (as BatchRun.row_scope)
        location_id = kwargs.get('accountDistrict') or kwargs.get('accountRegion')
        if location_id and not batch_location_allowed(user, location_id):
            return []
        sql_params, params = batchRunSummaryFilter(**kwargs)
        if settings.MSSQL:
            sql = '''
            SELECT
                HashBytes('MD5', CONCAT(
                    b.RunID, '_',p.ProdID, '_', r.RelIndexID
                )),
                b.RunYear,
                b.RunMonth,
                p.ProductCode,
                p.ProductName,
                r.RelCareType,
                convert(varchar, r.CalcDate, 23),
                r.RelIndex
            FROM
                tblRelIndex r,
                tblLocations l,
                tblBatchRun b,
                tblProduct p
            WHERE
                r.LocationId = l.LocationId AND
                l.LocationId = b.LocationId AND
                r.ProdID = p.ProdID AND %s
            ORDER BY
                b.RunYear,
                b.RunMonth;
            ''' % sql_params
        else:
            sql = '''
                SELECT
                    MD5(CONCAT(
                        b."RunID", '_',p."ProdID", '_', r."RelIndexID"
                    )),
                    b."RunYear",
                    b."RunMonth",
                    p."ProductCode",
                    p."ProductName",
                    r."RelCareType",
                    CAST (r."CalcDate" as varchar(23)),
                    r."RelIndex"
                FROM
                    "tblRelIndex" r,
                    "tblLocations" l,
                    "tblBatchRun" b,
                    "tblProduct" p
                WHERE
                    r."LocationId" = l."LocationId" AND
                    l."LocationId" = b."LocationId" AND
                    r."ProdID" = p."ProdID" AND %s
                ORDER BY
                    b."RunYear",
                    b."RunMonth";
            ''' % sql_params
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            res = [BatchRunSummaryGQLType(
                id=r[0],
                run_year=r[1],
                run_month=r[2],
                product_label=f'{r[3]} {r[4]}',
                care_type=r[5],
                calc_date=r[6],
                index=r[7]
            ) for r in cursor.fetchall()]
            return res

    def resolve_relative_indexes(self, info, **kwargs):
        # an empty right list would let anyone read: the batch run search right then
        perms = ClaimBatchConfig.gql_query_relative_indexes_perms \
            or ClaimBatchConfig.gql_query_batch_runs_perms
        user = info.context.user
        if not user.is_authenticated or not user.has_perms(perms):
            raise PermissionDenied(_("unauthorized"))
        # the indexes of the user's locations (and national ones) by
        # RelativeIndex.get_queryset (RelativeIndexGQLType); current ones (OP-2029)
        legacy_row_filters(info, kwargs, validity=Validity.NOW)
        return RelativeIndex.objects.all()


class Mutation(graphene.ObjectType):
    process_batch = ProcessBatchMutation.Field()
