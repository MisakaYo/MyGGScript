from datetime import datetime

from cached_property import cached_property

from module.config.deep import deep_get
from module.config.utils import filepath_argument, read_file
from module.logger import logger


class LogRes:
    def __init__(self, config):
        self.__dict__['config'] = config

    def __setattr__(self, key, value):
        if key not in self.groups:
            logger.info('No such resource on dashboard')
            super().__setattr__(key, value)
            return

        group_key = f'Dashboard.{key}'
        original = deep_get(self.config.data, keys=group_key, default={})
        record_time = datetime.now().replace(microsecond=0)

        if isinstance(value, dict):
            changed = False
            for field, field_value in value.items():
                if original.get(field) == field_value:
                    continue
                self.config.modified[f'{group_key}.{field}'] = field_value
                changed = True
            if changed:
                self.config.modified[f'{group_key}.Record'] = record_time
            return

        if original.get('Value') != value:
            self.config.modified[f'{group_key}.Value'] = value
            self.config.modified[f'{group_key}.Record'] = record_time

    @cached_property
    def groups(self):
        data = read_file(filepath_argument('dashboard'))
        return deep_get(data, keys='Dashboard', default=[])
