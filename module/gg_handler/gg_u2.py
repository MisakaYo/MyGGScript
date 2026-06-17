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
            device: 当前设备对象，需要提供 adb 与休眠能力。
        Returns:
            None
        Raises:
            Exception: 当 uiautomator2 连接设备失败时继续向上抛出。
        """
        super().__init__(config, device)
        self.config = config
        self.device = device
        self.factor = 200
        self.gg_package_name = deep_get(self.config.data, keys='GameManager.GGHandler.GGPackageName')
        self.d = u2.connect(self.device.serial)
        self.d.wait_timeout = 10.0
        self.gg_step_timeout = 8.0
        self.gg_poll_interval = 0.5

    def exit(self):
        """
        关闭 GG 应用。
        Args:
            None
        Returns:
            None
        Raises:
            Exception: 设备控制异常时继续向上抛出。
        """
        self.d.app_stop(self.gg_package_name)
        logger.attr('GG', 'Killed')

    def skip_error(self):
        """
        关闭 GG 残留弹窗，并返回是否看到了 GG 错误提示。
        Args:
            None
        Returns:
            bool: `True` 表示检测到需要跳过的 GG 弹窗，`False` 表示未检测到。
        Raises:
            Exception: UI 查询或关闭应用失败时继续向上抛出。
        """
        skipped = False
        if self.d.xpath('//*[@text="閲嶅惎娓告垙"]').exists:
            skipped = True
            logger.hr('Game died with GG panel')
        logger.info('Kill GG panel if it is still alive')
        self.exit()
        return skipped

    def _probe(self, stage, predicate):
        """
        安全探测一个 GG 界面条件。
        Args:
            stage: 当前探测阶段名，用于日志定位。
            predicate: 无参回调，返回布尔值表示条件是否满足。
        Returns:
            bool: `True` 表示条件满足，`False` 表示当前未满足或本轮读取 UI 失败。
        Raises:
            None
        """
        # GG 在切页或动画过程中，uiautomator2 偶尔会抛出读树空指针。
        # 这里把它当成本轮未命中处理，避免一次抖动就把整轮启用流程打断。
        try:
            return bool(predicate())
        except Exception as exc:
            logger.warning(f'{stage}: transient UI probe failed: {exc.__class__.__name__}')
            return False

    def _wait_until(self, stage, predicate, timeout=None, interval=None):
        """
        轮询等待一个 GG 阶段条件成立。
        Args:
            stage: 当前等待阶段名，会写入日志便于排查卡住位置。
            predicate: 无参回调，返回布尔值表示条件是否达成。
            timeout: 最长等待秒数；未传时使用默认阶段超时。
            interval: 轮询间隔秒数；未传时使用默认轮询间隔。
        Returns:
            bool: `True` 表示在超时前等到了目标条件，`False` 表示超时。
        Raises:
            None
        """
        # 每一步都只给有限等待，避免无限循环把真实卡点吞掉。
        # 同时保留适度容错，让慢一点的设备也能在窗口动画结束后继续往下走。
        timeout = self.gg_step_timeout if timeout is None else timeout
        interval = self.gg_poll_interval if interval is None else interval
        logger.info(f'Wait {stage} (timeout={timeout:.1f}s)')
        deadline = time() + timeout
        while time() < deadline:
            if self._probe(stage, predicate):
                logger.info(f'{stage}: ready')
                return True
            self.device.sleep(interval)
        logger.warning(f'{stage}: timeout after {timeout:.1f}s')
        return False

    def set_on(self, factor=200):
        """
        打开 GG 并执行倍率脚本。
        Args:
            factor: 默认写入脚本的倍率值。
        Returns:
            None
        Raises:
            Exception: 任一步骤失败时继续向上抛出，由上层处理重试或重启。
        """
        app_names = {
            'en': 'Azur Lane',
            'cn': '纰ц摑鑸嚎',
            'jp': '銈偤銉笺儷銉兗銉?',
            'tw': '纰ц棈鑸窔',
        }
        # 这里保留现有 GG 操作顺序，只给关键界面切换补等待与重试。
        # 这样不改变现有配置语义，但能减少“刚点箭头就开始读树”带来的偶发卡死。
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
                self._wait_until(
                    'GG panel body',
                    lambda: self.d(resourceId=f'{self.gg_package_name}:id/search_tab').exists
                    or self.d(resourceId=f'{self.gg_package_name}:id/search_toolbar').exists
                    or self.d(resourceId=f'{self.gg_package_name}:id/app_icon').exists,
                )
            else:
                self.d.app_start(self.gg_package_name)
                logger.info('Starting GG')
                self.device.sleep(3)

            app_name = app_names.get(self.config.SERVER, app_names['en'])
            logger.info(f'GG target app name: {app_name}')
            while True:
                self.device.sleep(self.gg_poll_interval)
                if self.d.xpath('//*[@text="蹇界暐"]').exists:
                    self.d.xpath('//*[@text="蹇界暐"]').click()
                    logger.info('Click ignore')
                    continue
                if self.d(resourceId=f'{self.gg_package_name}:id/btn_start_usage').exists:
                    self.d(resourceId=f'{self.gg_package_name}:id/btn_start_usage').click()
                    logger.attr('GG', 'Started')
                    self._wait_until(
                        'GG panel entry',
                        lambda: self.d(resourceId=f'{self.gg_package_name}:id/hot_point_icon').exists
                        or self.d(resourceId=f'{self.gg_package_name}:id/search_tab').exists
                        or self.d(resourceId=f'{self.gg_package_name}:id/search_toolbar').exists,
                    )
                    continue
                if self.d(resourceId=f'{self.gg_package_name}:id/hot_point_icon').exists:
                    self.d(resourceId=f'{self.gg_package_name}:id/hot_point_icon').click()
                    logger.info('Open GG panel')
                    self._wait_until(
                        'GG panel body',
                        lambda: self.d(resourceId=f'{self.gg_package_name}:id/search_tab').exists
                        or self.d(resourceId=f'{self.gg_package_name}:id/search_toolbar').exists
                        or self.d(resourceId=f'{self.gg_package_name}:id/app_icon').exists,
                    )
                    continue
                if self.d(resourceId=f'{self.gg_package_name}:id/search_tab').exists \
                        and not self.d(resourceId=f'{self.gg_package_name}:id/search_toolbar').exists:
                    self.d(resourceId=f'{self.gg_package_name}:id/search_tab').click()
                    logger.info('Switch to search tab')
                    self._wait_until(
                        'GG search toolbar',
                        lambda: self.d(resourceId=f'{self.gg_package_name}:id/search_toolbar').exists,
                    )
                    continue
                if self.d.xpath(
                    f'//*[@package="{self.gg_package_name}" and @resource-id="android:id/text1" '
                    f'and contains(@text,"{app_name}")]'
                ).exists:
                    self.d.xpath(f'//*[contains(@text,"{app_name}")]').click()
                    logger.info('Choose APP: Azur Lane')
                    chosen = True
                    self._wait_until(
                        'GG toolbar after app choose',
                        lambda: self.d(resourceId=f'{self.gg_package_name}:id/search_toolbar').exists,
                    )
                    continue
                if not chosen and self.d(resourceId=f'{self.gg_package_name}:id/app_icon').exists:
                    self.d(resourceId=f'{self.gg_package_name}:id/app_icon').click()
                    logger.info('Open APP chooser')
                    self._wait_until(
                        'GG app chooser',
                        lambda: self.d.xpath(
                            f'//*[@package="{self.gg_package_name}" and @resource-id="android:id/text1" '
                            f'and contains(@text,"{app_name}")]'
                        ).exists,
                    )
                    continue
                if self.d(resourceId=f'{self.gg_package_name}:id/search_toolbar').exists:
                    self.d.xpath(
                        f'//*[@resource-id="{self.gg_package_name}:id/search_toolbar"]/android.widget.ImageView[last()]'
                    ).click()
                    logger.info('Open script runner')
                    runner_ready = self._wait_until(
                        'GG script runner',
                        lambda: self.d(resourceId=f'{self.gg_package_name}:id/file').exists
                        or self.d.xpath('//*[@text="鎵ц"]').exists
                        or self.d.xpath('//*[contains(@text,"淇敼闈㈡澘")]').exists
                        or self.d(resourceId=f'{self.gg_package_name}:id/edit').exists,
                    )
                    if not runner_ready:
                        # 第一次点箭头时，部分设备只会把 GG 切到过渡态。
                        # 这里回到外层循环重试入口，比直接抛异常更贴近手动补点箭头后的恢复路径。
                        logger.warning('GG script runner did not become ready, retrying entry flow')
                        continue
                    if self._run():
                        return
                if self.d.xpath('//*[@text="鍙栨秷"]').exists:
                    self.d.xpath('//*[@text="鍙栨秷"]').click()
                    logger.info('Close previous script dialog')
                    continue
                if self.d.xpath('//*[@text="纭畾"]').exists:
                    self.d.xpath('//*[@text="纭畾"]').click()
                    logger.info('Confirm script dialog')
                    continue
                if self.d.xpath('//*[@text="閲嶅惎娓告垙"]').exists:
                    self.d.xpath('//*[@text="閲嶅惎娓告垙"]').click()
                    logger.info('Dismiss GG restart dialog')
        except Exception as exc:
            # 统一把 GG 启动阶段的异常打到主日志，方便从最后一个阶段日志反推卡住位置。
            logger.exception(exc)
            raise

    def _run(self):
        """
        推送并执行 GG Lua 脚本。
        Args:
            None
        Returns:
            bool: `True` 表示倍率脚本已执行完成并写回 GG 状态。
        Raises:
            FileNotFoundError: 本地 `bin/Lua/Multiplier.lua` 缺失时抛出。
            Exception: 设备交互或 UI 操作失败时继续向上抛出。
        """
        # 这里统一把 Lua 推到 /sdcard 根目录，兼容当前已经验证过的 GG 路径。
        # 先检查本地文件存在，再删除远端旧文件，避免资源缺失时把用户设备上的旧脚本先删掉。
        remote_path = '/sdcard/Multiplier.lua'
        local_path = Path('bin/Lua/Multiplier.lua')
        if not local_path.exists():
            logger.critical(f'GG Lua script missing: {local_path}')
            raise FileNotFoundError(f'GG Lua script missing: {local_path}')

        self.device.adb_shell(['rm', '-f', remote_path])
        self.device.adb_push(str(local_path), remote_path)
        logger.info('Lua pushed')

        file_path_set = False
        run_clicked = False
        option_opened = False
        factor_set = False
        confirmed = False
        confirmed_at = 0.0
        # 这里故意保留较长的确认后等待。
        # 原因是部分设备第二段进度条结束得比按钮退场更慢，过早关 GG 会让修改中断。
        confirm_grace_period = 45.0
        settle_started = False
        while True:
            self.device.sleep(1)
            if not file_path_set:
                if not self._wait_until(
                    'GG file input',
                    lambda: self.d(resourceId=f'{self.gg_package_name}:id/file').exists,
                ):
                    continue
                self.d(resourceId=f'{self.gg_package_name}:id/file').send_keys(remote_path)
                logger.info(f'Lua path set: {remote_path}')
                file_path_set = True
                continue
            if not run_clicked:
                if not self._wait_until(
                    'GG run button',
                    lambda: self.d.xpath('//*[@text="鎵ц"]').exists,
                ):
                    continue
                self.d.xpath('//*[@text="鎵ц"]').click()
                logger.info('Click run')
                run_clicked = True
                continue
            if not option_opened:
                if not self._wait_until(
                    'GG multiply option',
                    lambda: self.d.xpath('//*[contains(@text,"淇敼闈㈡澘")]').exists,
                ):
                    continue
                self.d.xpath('//*[contains(@text,"淇敼闈㈡澘")]').click()
                logger.info('Click multiply option')
                option_opened = True
                continue
            if not factor_set:
                # 输入框会在面板里停留一段时间，这里只写一次值。
                # 如果每轮都重写，会不断打断后续确认按钮的出现。
                if not self._wait_until(
                    'GG factor input',
                    lambda: self.d(resourceId=f'{self.gg_package_name}:id/edit').exists,
                ):
                    continue
                self.d(resourceId=f'{self.gg_package_name}:id/edit').send_keys(str(self.factor))
                logger.info('Factor set')
                factor_set = True
                continue
            if not confirmed:
                # 只有倍率写入完成后再等确认按钮，避免把其他旧弹窗误判成这一步的确认。
                if not self._wait_until(
                    'GG confirm button',
                    lambda: self.d.xpath('//*[@text="纭畾"]').exists,
                ):
                    continue
                self.d.xpath('//*[@text="纭畾"]').click()
                logger.info('Click confirm')
                confirmed = True
                confirmed_at = time()
                settle_started = False
                continue

            # 点击确认后，GG 可能还会继续执行一小段 Lua 与动画。
            # 这里先确认输入框和确认按钮已经退场，再追加固定保底等待，避免 Alas 提前把前台切回游戏。
            has_edit = self._probe(
                'GG settle edit probe',
                lambda: self.d(resourceId=f'{self.gg_package_name}:id/edit').exists,
            )
            has_confirm = self._probe(
                'GG settle confirm probe',
                lambda: self.d.xpath('//*[@text="纭畾"]').exists,
            )
            if has_edit or has_confirm:
                settle_started = False
                continue

            if not settle_started:
                logger.info('Wait GG Lua settle')
                settle_started = True
                continue

            # 不依赖“按钮一消失就立刻结束”，而是补一段固定保底时间。
            # 副作用是启用 GG 会多等几十秒，但能显著降低未改完就被切回游戏的问题。
            if time() - confirmed_at < confirm_grace_period:
                continue

            GGData(self.config).set_data(target='gg_on', value=True)
            logger.info(f'GG Lua settle finished after {confirm_grace_period:.0f}s grace period')
            logger.attr('GG', 'Enabled')
            logger.hr('GG Enabled', level=2)
            self.d.app_stop(self.gg_package_name)
            return True
