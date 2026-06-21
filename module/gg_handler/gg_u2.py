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
        gg_restart_text = '\u91cd\u542f\u6e38\u620f'
        skipped = False
        if self.d.xpath(f'//*[@text="{gg_restart_text}"]').exists:
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

    def _shell_quote(self, value):
        """
        将字符串转成可安全拼接到 `sh -c` 中的单引号字面量。
        Args:
            value: 需要传给 Android shell 的原始路径。
        Returns:
            str: 经过转义后的 shell 字面量。
        Raises:
            None
        """
        # 这里必须显式处理 `$MuMu12Shared` 这类路径。
        # 如果直接拼到 shell 字符串里，`$` 会被当成环境变量展开，导致明明存在的文件被判断成不存在。
        return "'" + value.replace("'", "'\"'\"'") + "'"

    def _iter_lua_remote_paths(self):
        """
        返回 GG Lua 脚本支持的远端候选路径。
        Args:
            None
        Returns:
            tuple[str, ...]: 按优先级排序的远端 Lua 路径列表。
        Raises:
            None
        """
        # 不同模拟器或共享目录挂载方式不同，这里固定维护一组候选路径。
        # 后续逻辑会优先使用设备上已存在的文件路径，只有都不存在时才补推送。
        return (
            '/sdcard/$MuMu12Shared/Multiplier.lua',
            '/sdcard/Multiplier.lua',
            '/sdcard/Notes/Multiplier.lua',
        )

    def _remote_path_exists(self, remote_path):
        """
        通过 adb 在设备侧判断某个远端 Lua 路径是否已存在。
        Args:
            remote_path: 需要检查的远端文件路径。
        Returns:
            bool: `True` 表示文件已存在，`False` 表示不存在或本轮检查失败。
        Raises:
            None
        """
        # 这里先在 adb 侧判断文件是否存在，而不是让 GG 文件输入框试错。
        # 好处是能避开 GG UI 可见性差异，先把真正存在的路径选出来，再进行后续按钮流程。
        quoted = self._shell_quote(remote_path)
        command = f'if [ -f {quoted} ]; then echo 1; else echo 0; fi'
        try:
            result = self.device.adb_shell(['sh', '-c', command])
        except Exception as exc:
            logger.warning(f'Check Lua path failed: {remote_path} ({exc.__class__.__name__})')
            return False

        if isinstance(result, bytes):
            result = result.decode('utf-8', errors='ignore')
        result = str(result).strip()
        exists = result.endswith('1')
        logger.info(f'Lua path exists={exists}: {remote_path}')
        return exists

    def _push_lua_candidates(self, local_path):
        """
        将本地 Lua 脚本推送到所有候选远端位置。
        Args:
            local_path: 本地 Lua 文件路径。
        Returns:
            None
        Raises:
            Exception: 当 adb 创建目录或推送文件失败时继续向上抛出。
        """
        # 这里采用“全量预推送”而不是只推某一个目录。
        # 原因是不同模拟器暴露给 GG 的可见目录不同，先把候选位置都准备好，后面才能稳定命中。
        for remote_path in self._iter_lua_remote_paths():
            remote_dir = remote_path.rsplit('/', 1)[0]
            self.device.adb_shell(['mkdir', '-p', remote_dir])
            self.device.adb_shell(['rm', '-f', remote_path])
            self.device.adb_push(str(local_path), remote_path)
            logger.info(f'Lua pushed to {remote_path}')

    def _resolve_remote_lua_path(self, local_path):
        """
        解析本次应填给 GG 的远端 Lua 路径。
        Args:
            local_path: 本地 Lua 文件路径，仅在需要补推送时使用。
        Returns:
            str: 最终选中的远端 Lua 路径。
        Raises:
            Exception: 当补推送阶段失败时继续向上抛出。
        """
        # 这里优先信任设备上已经存在的文件，避免每次都重推覆盖用户手动放进去的版本。
        # 如果三个路径都不存在，再统一推送一轮并重新检查，尽量把“准备资源”和“驱动 GG UI”分离。
        for remote_path in self._iter_lua_remote_paths():
            if self._remote_path_exists(remote_path):
                logger.info(f'Use existing Lua path: {remote_path}')
                return remote_path

        logger.warning('No existing Lua path found on device, pushing Lua to candidate paths')
        self._push_lua_candidates(local_path)

        for remote_path in self._iter_lua_remote_paths():
            if self._remote_path_exists(remote_path):
                logger.info(f'Use pushed Lua path: {remote_path}')
                return remote_path

        # 理论上推送成功后至少应该命中一个候选路径。
        # 如果仍全部不存在，直接抛错比继续让 GG 盲填路径更容易排查。
        raise FileNotFoundError('GG Lua script was pushed but no remote candidate path is visible on device')

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
            'cn': '\u78a7\u84dd\u822a\u7ebf',
            'jp': '\u30a2\u30ba\u30fc\u30eb\u30ec\u30fc\u30f3',
            'tw': '\u78a7\u85cd\u822a\u7dda',
        }
        gg_ignore_text = '\u5ffd\u7565'
        gg_cancel_text = '\u53d6\u6d88'
        gg_confirm_text = '\u786e\u5b9a'
        gg_restart_text = '\u91cd\u542f\u6e38\u620f'
        # 这里把 GG 依赖的中文文案统一改成 Unicode 转义常量。
        # 原因是上一次源码已被错误编码污染，继续直接写中文文本会让“看起来像日志乱码”的问题变成真实匹配失败。
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
                if self.d.xpath(f'//*[@text="{gg_ignore_text}"]').exists:
                    self.d.xpath(f'//*[@text="{gg_ignore_text}"]').click()
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
                        or self.d.xpath('//*[@text="\u6267\u884c"]').exists
                        or self.d.xpath('//*[contains(@text,"\u4fee\u6539\u9762\u677f")]').exists
                        or self.d(resourceId=f'{self.gg_package_name}:id/edit').exists,
                    )
                    if not runner_ready:
                        # 第一次点箭头时，部分设备只会把 GG 切到过渡态。
                        # 这里回到外层循环重试入口，比直接抛异常更贴近手动补点箭头后的恢复路径。
                        logger.warning('GG script runner did not become ready, retrying entry flow')
                        continue
                    if self._run():
                        return
                if self.d.xpath(f'//*[@text="{gg_cancel_text}"]').exists:
                    self.d.xpath(f'//*[@text="{gg_cancel_text}"]').click()
                    logger.info('Close previous script dialog')
                    continue
                if self.d.xpath(f'//*[@text="{gg_confirm_text}"]').exists:
                    self.d.xpath(f'//*[@text="{gg_confirm_text}"]').click()
                    logger.info('Confirm script dialog')
                    continue
                if self.d.xpath(f'//*[@text="{gg_restart_text}"]').exists:
                    self.d.xpath(f'//*[@text="{gg_restart_text}"]').click()
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
        local_path = Path('bin/Lua/Multiplier.lua')
        if not local_path.exists():
            logger.critical(f'GG Lua script missing: {local_path}')
            raise FileNotFoundError(f'GG Lua script missing: {local_path}')

        gg_run_text = '\u6267\u884c'
        gg_confirm_text = '\u786e\u5b9a'
        gg_multiply_text = '\u4fee\u6539\u9762\u677f'
        gg_script_finished_text = '\u811a\u672c\u5df2\u7ed3\u675f'
        # 这里同样使用 Unicode 转义去匹配运行、确认和倍率面板文本。
        # 这样即使源码再次经过不同编码链路，运行时拿到的仍是正确中文，不会再把 GG 界面识别带坏。
        remote_path = self._resolve_remote_lua_path(local_path)

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
                    lambda: self.d.xpath(f'//*[@text="{gg_run_text}"]').exists,
                ):
                    continue
                self.d.xpath(f'//*[@text="{gg_run_text}"]').click()
                logger.info('Click run')
                run_clicked = True
                continue
            if not option_opened:
                if not self._wait_until(
                    'GG multiply option',
                    lambda: self.d.xpath(f'//*[contains(@text,"{gg_multiply_text}")]').exists,
                ):
                    continue
                self.d.xpath(f'//*[contains(@text,"{gg_multiply_text}")]').click()
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
                    lambda: self.d.xpath(f'//*[@text="{gg_confirm_text}"]').exists,
                ):
                    continue
                self.d.xpath(f'//*[@text="{gg_confirm_text}"]').click()
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
                lambda: self.d.xpath(f'//*[@text="{gg_confirm_text}"]').exists,
            )
            has_script_finished = self._probe(
                'GG settle script finished probe',
                lambda: self.d.xpath(f'//*[contains(@text,\"{gg_script_finished_text}\")]').exists,
            )
            # GG 某些版本会在倍率脚本收尾时再弹一次“脚本已结束”确认框。
            # 如果这里只是等待“确定”自己消失，Alas 会一直卡在 GG 前台，最后把它当成 unknown page。
            # 因此这里在确认看到结束文案后主动补点一次确定，兼容二次确认而不影响前面的倍率输入确认。
            if has_confirm and has_script_finished:
                self.d.xpath(f'//*[@text=\"{gg_confirm_text}\"]').click()
                logger.info('Confirm GG finished dialog')
                settle_started = False
                self.device.sleep(1)
                continue
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
