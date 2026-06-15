from pathlib import Path
from time import time

import uiautomator2 as u2

from module.base.base import ModuleBase as Base
from module.config.deep import deep_get
from module.gg_handler.gg_data import GGData
from module.logger import logger


class GGU2(Base):
    def __init__(self, config, device):
        """
        基于 UiAutomator2 驱动 GG 的最小执行器。

        Args:
            config: 当前 Alas 配置对象。
            device: 当前设备对象，需提供 adb 与截图能力。

        Returns:
            None

        Raises:
            Exception: 当 uiautomator2 连接设备失败时，会沿用上层异常处理链。
        """
        super().__init__(config, device)
        self.config = config
        self.device = device
        self.factor = 200
        self.gg_package_name = deep_get(self.config.data, keys='GameManager.GGHandler.GGPackageName')
        self.d = u2.connect(self.device.serial)
        self.d.wait_timeout = 10.0

    def exit(self):
        """
        关闭 GG 应用。

        Args:
            None

        Returns:
            None

        Raises:
            Exception: 设备控制异常时由上层接管。
        """
        self.d.app_stop(self.gg_package_name)
        logger.attr('GG', 'Killed')

    def skip_error(self):
        """
        关闭 GG 遗留弹窗，并返回是否看到了“重启游戏”类提示。

        Args:
            None

        Returns:
            bool: `True` 表示看到了 GG 错误弹窗，`False` 表示没有。

        Raises:
            Exception: UI 查询或关闭应用失败时由上层接管。
        """
        skipped = False
        if self.d.xpath('//*[@text="重启游戏"]').exists:
            skipped = True
            logger.hr('Game died with GG panel')
        logger.info('Kill GG panel if it is still alive')
        self.exit()
        return skipped

    def set_on(self, factor=200):
        """
        打开 GG 并执行倍率脚本。

        Args:
            factor: 默认写入脚本的倍率值。

        Returns:
            None

        Raises:
            Exception: 任一步骤失败时交给上层超时/重启逻辑处理。
        """
        app_names = {
            'en': 'Azur Lane',
            'cn': '碧蓝航线',
            'jp': 'アズールレーン',
            'tw': '碧藍航線',
        }
        # 这里保留旧 GG 面板驱动顺序，只修正选择器写法并补足阶段日志。
        # 这样不会改变用户现有配置的行为，但能避免 GG 在首轮轮询就因调用方式错误直接退出。
        try:
            self.factor = factor
            ggdata = GGData(self.config).get_data()
            if ggdata['gg_on']:
                logger.attr('GG', 'Enabled')
                return

            chosen = False
            if self.d(resourceId=f'{self.gg_package_name}:id/hot_point_icon').exists:
                self.d(resourceId=f'{self.gg_package_name}:id/hot_point_icon').click()
                logger.info('Open GG panel')
                self.device.sleep(0.5)
            else:
                self.d.app_start(self.gg_package_name)
                logger.info('Starting GG')
                self.device.sleep(3)

            app_name = app_names.get(self.config.SERVER, app_names['en'])
            logger.info(f'GG target app name: {app_name}')
            while True:
                self.device.sleep(0.5)
                if self.d.xpath('//*[@text="忽略"]').exists:
                    self.d.xpath('//*[@text="忽略"]').click()
                    logger.info('Click ignore')
                    continue
                # 这里必须使用 u2 的选择器调用形式，不能写成 `self.d.resourceId(...)`。
                # 否则 GG 打开后第一轮轮询就会抛属性错误，主流程只会看到“Starting GG -> SET_ON: DONE”的假象。
                if self.d(resourceId=f'{self.gg_package_name}:id/btn_start_usage').exists:
                    self.d(resourceId=f'{self.gg_package_name}:id/btn_start_usage').click()
                    logger.attr('GG', 'Started')
                    continue
                if self.d(resourceId=f'{self.gg_package_name}:id/hot_point_icon').exists:
                    self.d(resourceId=f'{self.gg_package_name}:id/hot_point_icon').click()
                    logger.info('Open GG panel')
                    continue
                if self.d(resourceId=f'{self.gg_package_name}:id/search_tab').exists \
                        and not self.d(resourceId=f'{self.gg_package_name}:id/search_toolbar').exists:
                    self.d(resourceId=f'{self.gg_package_name}:id/search_tab').click()
                    logger.info('Switch to search tab')
                    continue
                if self.d.xpath(
                    f'//*[@package="{self.gg_package_name}" and @resource-id="android:id/text1" and contains(@text,"{app_name}")]'
                ).exists:
                    self.d.xpath(f'//*[contains(@text,"{app_name}")]').click()
                    logger.info('Choose APP: Azur Lane')
                    chosen = True
                    continue
                if not chosen and self.d(resourceId=f'{self.gg_package_name}:id/app_icon').exists:
                    self.d(resourceId=f'{self.gg_package_name}:id/app_icon').click()
                    logger.info('Open APP chooser')
                    continue
                if self.d(resourceId=f'{self.gg_package_name}:id/search_toolbar').exists:
                    self.d.xpath(
                        f'//*[@resource-id="{self.gg_package_name}:id/search_toolbar"]/android.widget.ImageView[last()]'
                    ).click()
                    logger.info('Open script runner')
                    if self._run():
                        return
                if self.d.xpath('//*[@text="取消"]').exists:
                    self.d.xpath('//*[@text="取消"]').click()
                    logger.info('Close previous script dialog')
                    continue
                if self.d.xpath('//*[@text="确定"]').exists:
                    self.d.xpath('//*[@text="确定"]').click()
                    logger.info('Confirm script dialog')
                    continue
                if self.d.xpath('//*[@text="重启游戏"]').exists:
                    self.d.xpath('//*[@text="重启游戏"]').click()
                    logger.info('Dismiss GG restart dialog')
        except Exception as exc:
            # 统一把 GG 启动阶段的异常打到主日志，便于后续根据最后一个阶段日志定位是首页、选进程还是脚本窗口出错。
            logger.exception(exc)
            raise

    def _run(self):
        """
        推送并执行 GG Lua 脚本。

        Args:
            None

        Returns:
            bool: `True` 表示倍率脚本已完成并写回 GG 状态。

        Raises:
            FileNotFoundError: 本地 `bin/Lua/Multiplier.lua` 缺失时抛出明确错误。
            Exception: 设备交互或 UI 操作失败时由上层接管。
        """
        # 这里统一把 Lua 推到 /sdcard 根目录，兼容当前使用的 GG 路径。
        # 先检查本地文件存在，再删除远端旧文件，避免资源缺失时把用户现有脚本先删掉。
        remote_path = '/sdcard/Multiplier.lua'
        local_path = Path('bin/Lua/Multiplier.lua')
        if not local_path.exists():
            logger.critical(f'GG Lua script missing: {local_path}')
            raise FileNotFoundError(f'GG Lua script missing: {local_path}')

        self.device.adb_shell(['rm', '-f', remote_path])
        self.device.adb_push(str(local_path), remote_path)
        logger.info('Lua pushed')

        set_done = False
        confirmed = False
        confirmed_at = 0.0
        confirm_grace_period = 15.0
        settle_started = False
        while True:
            self.device.sleep(1)
            if self.d(resourceId=f'{self.gg_package_name}:id/file').exists:
                self.d(resourceId=f'{self.gg_package_name}:id/file').send_keys(remote_path)
                logger.info('Lua path set')
            if self.d.xpath('//*[@text="执行"]').exists:
                self.d.xpath('//*[@text="执行"]').click()
                logger.info('Click run')
                continue
            if self.d.xpath('//*[contains(@text,"修改面板")]').exists:
                self.d.xpath('//*[contains(@text,"修改面板")]').click()
                logger.info('Click multiply option')
                continue
            # 倍率输入框在 GG 弹窗里会持续存在一段时间。
            # 这里必须只在首次进入时写一次值，否则每轮都会重新聚焦输入框，导致下面的“确定”分支永远没有机会执行。
            if not set_done and self.d(resourceId=f'{self.gg_package_name}:id/edit').exists:
                self.d(resourceId=f'{self.gg_package_name}:id/edit').send_keys(str(self.factor))
                logger.info('Factor set')
                set_done = True
                continue
            # 只有确认输入框已经写值后，才进入确认阶段。
            # 这样可以把“还没填值”和“填值后等待确定按钮出现”区分开，便于后续根据日志定位 GG UI 变化。
            if set_done and self.d.xpath('//*[@text="确定"]').exists:
                self.d.xpath('//*[@text="确定"]').click()
                logger.info('Click confirm')
                confirmed = True
                confirmed_at = time()
                settle_started = False
                continue
            if set_done and confirmed:
                # 点下“确定”后，GG 还会继续执行 Lua 一小段时间。
                # 这里必须等输入框和确认按钮都退出当前前台流程，再额外等待一个短暂稳定窗口，
                # 否则 Alas 会过早关闭 GG，导致脚本修改被中断。
                has_edit = self.d(resourceId=f'{self.gg_package_name}:id/edit').exists
                has_confirm = self.d.xpath('//*[@text="确定"]').exists
                if has_edit or has_confirm:
                    settle_started = False
                    continue

                if not settle_started:
                    logger.info('Wait GG Lua settle')
                    settle_started = True
                    continue

                # 这里改成“确认后固定保底等待”而不只依赖界面消失，
                # 因为日志已经证明 Lua 在按钮收起后仍会继续跑一段时间，过早关闭 GG 会直接中断修改。
                # 副作用是每次启用 GG 会额外慢十几秒，但能显著降低倍率尚未写完就被 Alas 抢回前台的问题。
                if time() - confirmed_at < confirm_grace_period:
                    continue

                GGData(self.config).set_data(target='gg_on', value=True)
                logger.attr('GG', 'Enabled')
                logger.hr('GG Enabled', level=2)
                self.d.app_stop(self.gg_package_name)
                return True
