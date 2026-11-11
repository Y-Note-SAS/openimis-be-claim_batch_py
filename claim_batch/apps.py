from django.apps import AppConfig

MODULE_NAME = "claim_batch"

DEFAULT_CFG = {
    "gql_query_batch_runs_perms": ["111102"],
    # no dedicated right: the batch run search one (an empty list let anyone read)
    "gql_query_relative_indexes_perms": ["111102"],
    "gql_mutation_process_batch_perms": ["111101"],
    "reports_capitation_payment_perms": ["131218"],
    "account_preview_perms": ["111103"]
}


class ClaimBatchConfig(AppConfig):
    name = MODULE_NAME

    gql_query_batch_runs_perms = []
    gql_query_relative_indexes_perms = []
    gql_mutation_process_batch_perms = []
    reports_capitation_payment_perms = []
    account_preview_perms = []

    def __load_config(self, cfg):
        for field in cfg:
            if hasattr(ClaimBatchConfig, field):
                setattr(ClaimBatchConfig, field, cfg[field])

    def ready(self):
        from core.models import ModuleConfiguration
        cfg = ModuleConfiguration.get_or_default(MODULE_NAME, DEFAULT_CFG)
        self.__load_config(cfg)
