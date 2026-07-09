from module.gg_handler.gg_handler import GGHandler
from module.handler.login import LoginHandler
from module.logger import logger


class GameManager(LoginHandler):
    def run(self):
        logger.hr('Force Stop AzurLane', level=1)
        self.device.app_stop()
        logger.info('Force Stop finished')
        # 强制结束游戏后立即同步 GG 配置，避免下次冷启动时沿用过期倍率状态。
        GGHandler(config=self.config, device=self.device).check_config()

        if self.config.GameManager_AutoRestart:
            self.device.app_start()
            self.handle_app_login()


if __name__ == '__main__':
    GameManager('alas', task='GameManager').run()
