import uuid

from core import fields
from core import models as core_models
from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy
from location import models as location_models
from location.models import HealthFacility, Location
from product import models as product_models
from product.models import Product


#: a batch run / relative index is run for a region, a district or nationally (null)
BATCH_LOCATION_TYPES = ("R", "D")


def _location_scoped(cls, queryset, user, validity, audits):
    """
    The rows of `cls` whose location the user may see; a null location (national) stays
    visible, as in build_user_location_filter_query. Current rows unless validity /
    audits ask otherwise (OP-2029).
    """
    queryset = cls.apply_row_filters(queryset, validity, audits, default=cls.filter_queryset)
    user = cls.scoping_user(user)
    if settings.ROW_SECURITY and (user is None or getattr(user, "is_anonymous", True)):
        return queryset.none()
    if not settings.ROW_SECURITY:
        return queryset
    return cls.row_scope.filter(queryset, user, cls)


class BatchRun(core_models.VersionedModel):
    # seen by whoever sees its region / district (a region only with all its districts);
    # a national run (no location) by every user holding the right
    row_scope = core_models.LocationScope("location", loc_types=BATCH_LOCATION_TYPES)

    id = models.AutoField(db_column='RunID', primary_key=True)
    location = models.ForeignKey(
        location_models.Location, models.DO_NOTHING,
        db_column='LocationId', blank=True, null=True)
    run_date = fields.DateTimeField(db_column='RunDate')
    audit_user_id = models.IntegerField(db_column='AuditUserID')
    run_year = models.IntegerField(db_column='RunYear')
    run_month = models.SmallIntegerField(db_column='RunMonth')

    @classmethod
    def get_queryset(cls, queryset, user=None, *, validity=None, audits=None):
        return _location_scoped(cls, queryset, user, validity, audits)

    class Meta:
        managed = True
        db_table = 'tblBatchRun'


class RelativeIndex(core_models.VersionedModel):
    # same rule as the batch run computing it: its region / district, national = null
    row_scope = core_models.LocationScope("location", loc_types=BATCH_LOCATION_TYPES)

    id = models.AutoField(db_column='RelIndexID', primary_key=True)
    product = models.ForeignKey(
        product_models.Product, models.DO_NOTHING, db_column='ProdID')
    type = models.SmallIntegerField(db_column='RelType')
    care_type = models.CharField(db_column='RelCareType', max_length=1)
    year = models.IntegerField(db_column='RelYear')
    period = models.SmallIntegerField(db_column='RelPeriod')
    calc_date = models.DateTimeField(db_column='CalcDate')
    rel_index = models.DecimalField(
        db_column='RelIndex', max_digits=18, decimal_places=4, blank=True, null=True)
    audit_user_id = models.IntegerField(db_column='AuditUserID')
    location = models.ForeignKey(
        location_models.Location, models.DO_NOTHING, db_column='LocationId', blank=True, null=True,
        related_name="relative_indexes"
    )

    @classmethod
    def get_queryset(cls, queryset, user=None, *, validity=None, audits=None):
        return _location_scoped(cls, queryset, user, validity, audits)

    class Meta:
        managed = True
        db_table = 'tblRelIndex'

    CARE_TYPE_OUT_PATIENT = "O"
    CARE_TYPE_IN_PATIENT = "I"
    CARE_TYPE_BOTH = "B"

    TYPE_MONTH = 12
    TYPE_QUARTER = 4
    TYPE_YEAR = 1


class RelativeDistribution(models.Model):
    CARE_TYPE_OUT_PATIENT = "O"
    CARE_TYPE_IN_PATIENT = "I"
    CARE_TYPE_BOTH = "B"

    TYPE_MONTH = 12
    TYPE_QUARTER = 4
    TYPE_YEAR = 1

    id = models.AutoField(db_column='DistrID', primary_key=True)
    product = models.ForeignKey(product_models.Product, models.DO_NOTHING, db_column='ProdID',
                                related_name="relative_distributions")
    type = models.SmallIntegerField(db_column='DistrType', choices=((TYPE_MONTH, gettext_lazy("Month")), (TYPE_QUARTER, gettext_lazy("Quarter")), (TYPE_YEAR, gettext_lazy('Year'))))
    care_type = models.CharField(db_column='DistrCareType', max_length=1, choices=((CARE_TYPE_BOTH, gettext_lazy("Both")), (CARE_TYPE_IN_PATIENT, gettext_lazy("In-Patient")), (CARE_TYPE_OUT_PATIENT, gettext_lazy("Out-Patient"))))
    period = models.SmallIntegerField(db_column='Period')
    percent = models.DecimalField(
        db_column='DistrPerc', max_digits=18, decimal_places=2, blank=True, null=True)

    validity_from = models.DateTimeField(db_column='ValidityFrom')
    validity_to = models.DateTimeField(
        db_column='ValidityTo', blank=True, null=True)
    legacy_id = models.IntegerField(
        db_column='LegacyID', blank=True, null=True)
    audit_user_id = models.IntegerField(db_column='AuditUserID')

    class Meta:
        managed = True
        db_table = 'tblRelDistr'


    CARE_TYPE_OUT_PATIENT = "O"
    CARE_TYPE_IN_PATIENT = "I"
    CARE_TYPE_BOTH = "B"

    TYPE_MONTH = 12
    TYPE_QUARTER = 4
    TYPE_YEAR = 1


class CapitationPayment(core_models.VersionedModel):
    id = models.AutoField(db_column='CapitationPaymentID', primary_key=True)
    uuid = models.CharField(db_column='CapitationPaymentUUID', max_length=36, default=uuid.uuid4, unique=True)

    year = models.IntegerField('Year', null=False)
    month = models.IntegerField('Month', null=False)
    product = models.ForeignKey(Product, models.DO_NOTHING, db_column='ProductID',
                                related_name="capitation_payment_product")

    health_facility = models.ForeignKey(HealthFacility, models.DO_NOTHING, db_column='HfID',
                                        related_name="capitation_payment_health_facility")

    region_code = models.CharField(db_column='RegionCode', max_length=8, null=True, blank=True)
    region_name = models.CharField(db_column='RegionName', max_length=50, null=True, blank=True)

    district_code = models.CharField(db_column='DistrictCode', max_length=8, null=True, blank=True)
    district_name = models.CharField(db_column='DistrictName', max_length=50, null=True, blank=True)

    health_facility_code = models.CharField(db_column='HFCode', max_length=8)
    health_facility_name = models.CharField(db_column='HFName', max_length=100)

    acc_code = models.CharField(db_column='AccCode', max_length=25, null=True, blank=True)

    hf_level = models.CharField(db_column='HFLevel', max_length=100, blank=True, null=True)
    hf_sublevel = models.CharField(db_column='HFSublevel', max_length=100, blank=True, null=True)

    total_population = models.DecimalField(
        db_column='TotalPopulation', max_digits=18, decimal_places=2, blank=True, null=True, default=0)

    total_families = models.DecimalField(
        db_column='TotalFamilies', max_digits=18, decimal_places=2, blank=True, null=True, default=0)

    total_insured_insuree = models.DecimalField(
        db_column='TotalInsuredInsuree', max_digits=18, decimal_places=2, blank=True, null=True, default=0)

    total_insured_families = models.DecimalField(
        db_column='TotalInsuredFamilies', max_digits=18, decimal_places=2, blank=True, null=True, default=0)

    total_claims = models.DecimalField(
        db_column='TotalClaims', max_digits=18, decimal_places=2, blank=True, null=True, default=0)

    alc_contri_population = models.DecimalField(
        db_column='AlcContriPopulation', max_digits=18, decimal_places=2, blank=True, null=True, default=0)

    alc_contri_num_families = models.DecimalField(
        db_column='AlcContriNumFamilies', max_digits=18, decimal_places=2, blank=True, null=True, default=0)

    alc_contri_ins_population = models.DecimalField(
        db_column='AlcContriInsPopulation', max_digits=18, decimal_places=2, blank=True, null=True, default=0)

    alc_contri_ins_families = models.DecimalField(
        db_column='AlcContriInsFamilies', max_digits=18, decimal_places=2, blank=True, null=True, default=0)

    alc_contri_visits = models.DecimalField(
        db_column='AlcContriVisits', max_digits=18, decimal_places=2, blank=True, null=True, default=0)

    alc_contri_adjusted_amount = models.DecimalField(
        db_column='AlcContriAdjustedAmount', max_digits=18, decimal_places=2, blank=True, null=True, default=0)

    up_population = models.DecimalField(
        db_column='UPPopulation', max_digits=18, decimal_places=2, blank=True, null=True, default=0)

    up_num_families = models.DecimalField(
        db_column='UPNumFamilies', max_digits=18, decimal_places=2, blank=True, null=True, default=0)

    up_ins_population = models.DecimalField(
        db_column='UPInsPopulation', max_digits=18, decimal_places=2, blank=True, null=True, default=0)

    up_ins_families = models.DecimalField(
        db_column='UPInsFamilies', max_digits=18, decimal_places=2, blank=True, null=True, default=0)

    up_visits = models.DecimalField(
        db_column='UPVisits', max_digits=18, decimal_places=2, blank=True, null=True, default=0)

    up_adjusted_amount = models.DecimalField(
        db_column='UPAdjustedAmount', max_digits=18, decimal_places=2, blank=True, null=True, default=0)

    payment_cathment = models.DecimalField(
        db_column='PaymentCathment', max_digits=18, decimal_places=2, blank=True, null=True, default=0)

    total_adjusted = models.DecimalField(
        db_column='TotalAdjusted', max_digits=18, decimal_places=2, blank=True, null=True, default=0)

    class Meta:
        db_table = 'tblCapitationPayment'

