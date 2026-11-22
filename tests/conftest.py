from __future__ import annotations

import gzip
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def write_als(path: Path, xml: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wb") as handle:
        handle.write(xml.encode("utf-8"))
    return path


def touch_audio(path: Path, content: bytes = b"audio") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def mixed_plugin_als() -> str:
    return """
    <Ableton Creator="Ableton Live 12.2">
      <LiveSet>
        <Tracks>
          <AudioTrack Id="1">
            <Name><EffectiveName Value="1-Audio" /><UserName Value="Vocals" /></Name>
            <DeviceChain><DeviceChain><Devices>
              <AuPluginDevice Id="10">
                <PluginDesc><AuPluginInfo><Name Value="Auto-Tune Pro" /></AuPluginInfo></PluginDesc>
              </AuPluginDevice>
              <AudioEffectGroupDevice Id="11"><Branches><AudioEffectBranch>
                <DeviceChain><AudioToAudioDeviceChain><Devices>
                  <PluginDevice Id="12">
                    <PluginDesc><Vst3PluginInfo><Name Value="Pro-Q 3" /></Vst3PluginInfo></PluginDesc>
                  </PluginDevice>
                </Devices></AudioToAudioDeviceChain></DeviceChain>
              </AudioEffectBranch></Branches></AudioEffectGroupDevice>
            </Devices></DeviceChain></DeviceChain>
          </AudioTrack>
          <MidiTrack Id="2">
            <Name><EffectiveName Value="Synth" /><UserName Value="" /></Name>
            <DeviceChain><DeviceChain><Devices>
              <PluginDevice Id="20">
                <PluginDesc><VstPluginInfo><PlugName Value="Serum" /></VstPluginInfo></PluginDesc>
              </PluginDevice>
            </Devices></DeviceChain></DeviceChain>
          </MidiTrack>
          <ReturnTrack Id="3">
            <Name><EffectiveName Value="Return A" /><UserName Value="Reverb" /></Name>
            <DeviceChain><DeviceChain><Devices>
              <PluginDevice Id="30"><UserName Value="Mystery Device" /></PluginDevice>
            </Devices></DeviceChain></DeviceChain>
          </ReturnTrack>
        </Tracks>
        <MainTrack Id="0">
          <Name><EffectiveName Value="Main" /><UserName Value="" /></Name>
          <DeviceChain><DeviceChain><Devices>
            <PluginDevice Id="40">
              <PluginDesc><Vst3PluginInfo><Name Value="Limiter" /></Vst3PluginInfo></PluginDesc>
            </PluginDevice>
          </Devices></DeviceChain></DeviceChain>
        </MainTrack>
      </LiveSet>
    </Ableton>
    """
