import argparse
import json
import logging
import tempfile
from pathlib import Path

import aiohttp
from yarl import URL

from unifi.cams.base import UnifiCamBase


class Reolink(UnifiCamBase):
    def __init__(self, args: argparse.Namespace, logger: logging.Logger) -> None:
        super().__init__(args, logger)
        self.snapshot_dir: str = tempfile.mkdtemp()
        self.motion_in_progress: bool = False
        self.substream = args.substream

    @classmethod
    def add_parser(cls, parser: argparse.ArgumentParser) -> None:
        super().add_parser(parser)
        parser.add_argument("--username", "-u", required=True, help="Camera username")
        parser.add_argument("--password", "-p", required=True, help="Camera password")
        parser.add_argument(
            "--channel",
            "-c",
            default=0,
            help="Camera channel (default 0, used for API motion det.)",
        )

        parser.add_argument(
            "--substream",
            "-s",
            default="main",
            type=str,
            choices=["main", "sub", "ext"],
            required=True,
            help="Camera rtsp url substream index main, sub, or ext",
        )

    async def get_snapshot(self) -> Path:
        img_file = Path(self.snapshot_dir, "screen.jpg")
        url = (
            f"http://{self.args.ip}"
            f"/cgi-bin/api.cgi?cmd=Snap&channel={self.args.channel}"
            f"&width=1920&height=1080&rs=0&user={self.args.username}"
            f"&password={self.args.password}"
        )
        self.logger.info(f"Grabbing snapshot: {url}")
        await self.fetch_to_file(url, img_file)
        return img_file

    async def run(self) -> None:
        url = (
            f"http://{self.args.ip}"
            f"/api.cgi?cmd=GetMdState&user={self.args.username}"
            f"&password={self.args.password}"
        )
        encoded_url = URL(url, encoded=True)

        body = (
            f'[{{ "cmd":"GetMdState", "param":{{ "channel":{self.args.channel} }} }}]'
        )
        while True:
            self.logger.info(f"Connecting to motion events API: {url}")
            try:
                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(None)
                ) as session:
                    while True:
                        async with session.post(encoded_url, data=body) as resp:
                            data = await resp.read()

                            try:
                                json_body = json.loads(data)
                                if "value" in json_body[0]:
                                    if json_body[0]["value"]["state"] == 1:
                                        if not self.motion_in_progress:
                                            self.motion_in_progress = True
                                            self.logger.info("Trigger motion start")
                                            await self.trigger_motion_start()
                                    elif json_body[0]["value"]["state"] == 0:
                                        if self.motion_in_progress:
                                            self.motion_in_progress = False
                                            self.logger.info("Trigger motion end")
                                            await self.trigger_motion_stop()
                                else:
                                    self.logger.error(
                                        "Motion API request responded with "
                                        "unexpected JSON, retrying. "
                                        f"JSON: {data}"
                                    )

                            except json.JSONDecodeError as err:
                                self.logger.error(
                                    "Motion API request returned invalid "
                                    "JSON, retrying. "
                                    f"Error: {err}, "
                                    f"Response: {data}"
                                )

            except aiohttp.ClientError as err:
                self.logger.error(f"Motion API request failed, retrying. Error: {err}")

    def get_stream_source(self, stream_index: str) -> str:
        return (
            f"rtsp://{self.args.username}:{self.args.password}@{self.args.ip}:554"
            f"//h264Preview_{int(self.args.channel) + 1:02}_{self.args.substream}"
        )
