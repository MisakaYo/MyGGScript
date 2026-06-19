from pathlib import Path

from module.config.deep import deep_get


class GGData:
    def __init__(self, config):
        self.config = config
        self.filepath = Path('./config/gg_handler') / f'gg_data_{self.config.config_name}.tmp'
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        self.ggdata = self._load()

    def _load(self):
        default = {
            'gg_on': False,
            'gg_enable': deep_get(self.config.data, keys='GameManager.GGHandler.Enabled', default=False),
            'gg_auto': deep_get(self.config.data, keys='GameManager.GGHandler.AutoRestartGG', default=False),
        }
        if not self.filepath.exists():
            self._write(default)
            return default

        lines = self.filepath.read_text(encoding='utf-8').splitlines()
        if not lines or lines[0] != self.config.config_name:
            self._write(default)
            return default

        data = default.copy()
        for line in lines[1:]:
            if '=' not in line:
                continue
            key, value = line.split('=', 1)
            data[key] = value == 'True'
        return data

    def _write(self, data):
        content = [self.config.config_name]
        content.extend(f'{key}={value}' for key, value in data.items())
        self.filepath.write_text('\n'.join(content) + '\n', encoding='utf-8')

    def get_data(self):
        return self.ggdata

    def set_data(self, target=None, value=None):
        self.ggdata[target] = value
        self._write(self.ggdata)
