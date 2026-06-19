from module.base.timer import timeout
from module.config.deep import deep_get, deep_set
from module.gg_handler.gg_data import GGData
from module.gg_handler.gg_u2 import GGU2
from module.logger import logger


class GGHandler:
    def __init__(self, config=None, device=None):
        self.config = config
        self.device = device
        self.factor = deep_get(self.config.data, keys='GameManager.GGHandler.GGMultiplyingFactor', default=200)

    def restart(self, crashed=False):
        from module.exception import GameStuckError
        from module.handler.login import LoginHandler
        from module.notify import handle_notify

        allow_crash_recovery = crashed
        for _ in range(2):
            try:
                if allow_crash_recovery:
                    timeout(self.handle_u2_restart, timeout_sec=60)
                if not timeout(LoginHandler(config=self.config, device=self.device).app_restart, timeout_sec=600):
                    break
                raise RuntimeError('GG restart timed out')
            except GameStuckError:
                pass
            except Exception as exc:
                logger.exception(exc)
                if allow_crash_recovery:
                    handle_notify(
                        self.config.Error_OnePushConfig,
                        title=f"Alas <{self.config.config_name}> crashed",
                        content=f"<{self.config.config_name}> RequestHumanTakeover.\nMaybe your emulator died.",
                    )
                    raise SystemExit(1)
                allow_crash_recovery = True

    def set(self, mode=True):
        logger.hr('Enabling GG')
        if mode:
            self.handle_u2_restart()
            failed = timeout(GGU2(config=self.config, device=self.device).set_on, timeout_sec=120, factor=self.factor)
            if failed:
                from module.exception import GameStuckError
                raise GameStuckError
        else:
            self.gg_reset()

    def skip_error(self):
        return GGU2(config=self.config, device=self.device).skip_error()

    def check_config(self):
        gg_enable = deep_get(self.config.data, keys='GameManager.GGHandler.Enabled', default=False)
        gg_auto = deep_get(self.config.data, keys='GameManager.GGHandler.AutoRestartGG', default=False)
        GGData(self.config).set_data(target='gg_enable', value=gg_enable)
        GGData(self.config).set_data(target='gg_auto', value=gg_auto)
        gg_data = GGData(self.config).get_data()
        logger.info('GG status:')
        logger.info(f'Enabled={gg_data["gg_enable"]} AutoRestart={gg_data["gg_auto"]} Current={gg_data["gg_on"]}')
        return gg_data

    def handle_u2_restart(self):
        if not deep_get(self.config.data, keys='GameManager.GGHandler.RestartATX', default=False):
            return

        from module.notify import handle_notify

        try:
            timeout(self.device.restart_atx, 60)
        except Exception:
            handle_notify(
                self.config.Error_OnePushConfig,
                title=f"Alas <{self.config.config_name}> Emulator error",
                content=f"<{self.config.config_name}> RequestHumanTakeover\nMaybe your emulator died",
            )
            raise SystemExit(1)

        import uiautomator2 as u2

        logger.info('Reset UiAutomator')
        try:
            u2.connect(self.device.serial).reset_uiautomator()
        except Exception:
            handle_notify(
                self.config.Error_OnePushConfig,
                title=f"Alas <{self.config.config_name}> Restart U2 failed",
                content=f"<{self.config.config_name}> RequestHumanTakeover.\nMaybe your emulator died",
            )
            raise SystemExit(1)

    def handle_restart(self):
        gg_data = GGData(config=self.config).get_data()
        if gg_data['gg_enable']:
            GGData(config=self.config).set_data(target='gg_on', value=False)
            logger.info('GG status:')
            logger.info(f'Enabled={gg_data["gg_enable"]} AutoRestart={gg_data["gg_auto"]} Current={gg_data["gg_on"]}')
            if not self.skip_error():
                logger.hr('Assume game died without GG panel')

    def gg_reset(self):
        gg_data = GGData(self.config).get_data()
        if gg_data['gg_enable'] and gg_data['gg_on']:
            logger.hr('Disabling GG')
            self.restart()
            logger.attr('GG', 'Disabled')

    def check_status(self, mode=True):
        gg_data = GGData(self.config).get_data()
        if not gg_data['gg_enable']:
            return

        gg_auto = mode if deep_get(self.config.data, keys='GameManager.GGHandler.AutoRestartGG', default=False) else False
        logger.info('Check GG status:')
        logger.info(f'Enabled={gg_data["gg_enable"]} AutoRestart={gg_data["gg_auto"]} Current={gg_data["gg_on"]}')
        if gg_auto:
            if not gg_data['gg_on']:
                self.set(True)
        elif gg_data['gg_on']:
            self.gg_reset()

    def power_limit(self, task=''):
        from module.gg_handler.assets import OCR_PRE_BATTLE_CHECK
        from module.ocr.ocr import Digit

        self.device.screenshot()
        ocr_check = Digit(OCR_PRE_BATTLE_CHECK, letter=(255, 255, 255), threshold=128)
        power = ocr_check.ocr(self.device.image)
        limit = deep_get(self.config.data, keys=f'GameManager.PowerLimit.{task}', default=17000)
        logger.attr('Power Limit', limit)
        if power < limit:
            return

        logger.critical("There's high chance that GG is on, restart to disable it")
        GGData(self.config).set_data(target='gg_on', value=False)
        GGData(self.config).set_data(target='gg_enable', value=True)
        deep_set(self.config.data, keys='GameManager.GGHandler.Enabled', value=True)
        deep_set(self.config.data, keys='GameManager.GGHandler.AutoRestartGG', value=True)
        self.config.task_call('Restart')
        self.config.task_delay(minute=0.5)
        self.config.task_stop('Restart for sake of safety')

    def handle_restart_before_tasks(self):
        gg_data = GGData(self.config).get_data()
        should_restart = deep_get(self.config.data, keys='GameManager.GGHandler.RestartEverytime', default=True)
        if should_restart and gg_data['gg_enable']:
            logger.info('Restart to reset GG status.')
            self.restart()
            return True
        return False

    def check_then_set_gg_status(self, task=''):
        disabled_task = deep_get(self.config.data, keys='GameManager.GGHandler.DisabledTask', default='disable_all_dangerous_task')
        group_exercise = ['exercise']
        group_meta = ['opsi_ash_assist', 'opsi_ash_beacon']
        group_raid = ['raid', 'raid_daily', 'coalition', 'coalition_sp']
        group_personal_choice = ['guild']
        group_enabled = [
            'hard', 'sos', 'war_archives', 'event_a', 'event_b', 'event_c', 'event_d', 'event_sp',
            'maritime_escort', 'opsi_explore', 'opsi_daily', 'opsi_obscure', 'opsi_month_boss',
            'opsi_abyssal', 'opsi_archive', 'opsi_stronghold', 'opsi_meowfficer_farming',
            'opsi_hazard1_leveling', 'opsi_cross_month', 'main', 'main2', 'main3', 'event', 'event2',
            'gems_farming', 'c72_mystery_farming', 'c122_medium_leveling', 'c124_large_leveling',
        ]

        if disabled_task == 'disable_meta_and_exercise':
            disabled = group_exercise + group_meta
            enabled = group_enabled + group_raid + group_personal_choice
        elif disabled_task == 'disable_exercise':
            disabled = group_exercise
            enabled = group_enabled + group_personal_choice + group_raid + group_meta
        elif disabled_task == 'enable_all':
            disabled = []
            enabled = group_enabled + group_personal_choice + group_raid + group_meta + group_exercise
        elif disabled_task == 'disable_guild_and_dangerous':
            disabled = group_exercise + group_meta + group_raid + group_personal_choice
            enabled = group_enabled
        else:
            disabled = group_exercise + group_meta + group_raid
            enabled = group_enabled + group_personal_choice

        if task in disabled:
            self.check_status(False)
        elif task in enabled:
            self.check_status(True)
