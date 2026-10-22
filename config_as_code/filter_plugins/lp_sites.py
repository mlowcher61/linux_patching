"""Filter for config_as_code: keep only objects for the enabled sites.

Objects in group_vars/aap/*.yml may carry a `site:` key (aws, vmware, cmdb).
Objects without one are shared and always kept.
"""


def lp_for_sites(items, enabled_sites):
    enabled = set(enabled_sites or [])
    return [item for item in items or [] if "site" not in item or item["site"] in enabled]


class FilterModule:
    def filters(self):
        return {"lp_for_sites": lp_for_sites}
