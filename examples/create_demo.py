"""在已绑定的测试项目中，经真实 MCP 协议建立可运行的资源验收场景。"""
import argparse
import asyncio
import json
import os
import sys
import wave
from pathlib import Path
from PIL import Image
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

VERTEX = '''attribute vec3 in_Position;
attribute vec4 in_Colour;
attribute vec2 in_TextureCoord;
varying vec2 v_vTexcoord;
varying vec4 v_vColour;
void main() {
    gl_Position = gm_Matrices[MATRIX_WORLD_VIEW_PROJECTION] * vec4(in_Position, 1.0);
    v_vColour = in_Colour;
    v_vTexcoord = in_TextureCoord;
}
'''
FRAGMENT = '''varying vec2 v_vTexcoord;
varying vec4 v_vColour;
void main() {
    gl_FragColor = v_vColour * texture2D(gm_BaseTexture, v_vTexcoord);
}
'''
CREATE = '''probe_message = scr_mcp_demo();
probe_alarm = false;
probe_frames = sprite_get_number(spr_mcp_demo);
probe_shader = shader_is_compiled(shd_mcp_demo);
probe_audio = audio_exists(sfx_mcp_demo);
alarm[0] = 1;
show_debug_message(probe_message);
'''
DRAW = '''draw_clear(make_color_rgb(16, 24, 36));
draw_set_color(c_white);
draw_text(32, 32, probe_message);
draw_text(32, 64, "Frames=" + string(probe_frames) + " Shader=" + string(probe_shader));
draw_text(32, 96, "Audio=" + string(probe_audio) + " Alarm=" + string(probe_alarm));
if (probe_shader) shader_set(shd_mcp_demo);
draw_sprite(spr_mcp_demo, 0, 64, 160);
draw_sprite(spr_mcp_demo, 1, 128, 160);
shader_reset();
'''


async def run(root, report):
    # 生成纯测试用图块与静音 WAV，不接触游戏美术或音频素材。
    inputs = root / '.mcp-demo-inputs'
    if inputs.exists():
        raise ValueError('演示输入目录已存在，拒绝覆盖；请使用新的测试项目')
    inputs.mkdir()
    frames = []
    for index, color in enumerate(((80, 200, 240, 255), (255, 160, 64, 255))):
        path = inputs / f'frame{index}.png'
        Image.new('RGBA', (32, 32), color).save(path)
        frames.append(str(path))
    wav = inputs / 'silent.wav'
    with wave.open(str(wav), 'wb') as sound:
        sound.setparams((1, 2, 44100, 0, 'NONE', 'not compressed'))
        sound.writeframes(b'\0\0' * 4410)
    params = StdioServerParameters(command=sys.executable,
        args=['-m', 'morx_gms2_mcp', 'serve', '--project', str(root), '--write'],
        env={**os.environ, 'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1'})
    results = []
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            async def call(name, arguments, write=False):
                if write:
                    preview = await session.call_tool(name, arguments={**arguments, 'dry_run': True})
                    if preview.isError:
                        raise RuntimeError(str(preview))
                    arguments = {**arguments, 'dry_run': False}
                result = await session.call_tool(name, arguments=arguments)
                if result.isError:
                    raise RuntimeError(str(result))
                data = json.loads(result.content[0].text)
                results.append({'tool': name, 'result': data})
                return data
            await call('server_info', {})
            await call('ensure_group', {'group': '脚本/测试'}, True)
            undo = await call('create_script', {'name': 'scr_mcp_undo', 'code': '// 仅测试恢复'}, True)
            await call('restore_backup', {'transaction_id': undo['transaction']}, True)
            await call('create_script', {'name': 'scr_mcp_demo', 'group': '脚本/测试',
                'code': 'function scr_mcp_demo() {\n\treturn "MCP_RESOURCE_V1_OK";\n}\n'}, True)
            await call('import_sprite', {'name': 'spr_mcp_demo', 'png_paths': frames, 'group': '精灵/测试'}, True)
            await call('import_sound', {'name': 'sfx_mcp_demo', 'wav_path': str(wav), 'group': '声音/测试'}, True)
            await call('create_shader', {'name': 'shd_mcp_demo', 'vertex_code': VERTEX, 'fragment_code': FRAGMENT}, True)
            await call('create_object', {'name': 'sys_mcp_demo'}, True)
            await call('configure_object', {'name': 'sys_mcp_demo', 'sprite': 'spr_mcp_demo', 'persistent': False}, True)
            for event, code in [('Create', CREATE), ('Draw', DRAW), ('DrawGUI', 'draw_text(32, 400, "GUI_EVENT_OK");\n'),
                                ('Alarm', 'probe_alarm = true;\n'), ('CleanUp', 'show_debug_message("CLEANUP_OK");\n')]:
                await call('add_object_event', {'object_name': 'sys_mcp_demo', 'event': event, 'code': code}, True)
            await call('create_room', {'name': 'rm_mcp_demo', 'width': 800, 'height': 450, 'group': '房间/测试'}, True)
            await call('add_instance_layer', {'room_name': 'rm_mcp_demo', 'layer_name': 'Controllers', 'depth': -100}, True)
            await call('place_object', {'object_name': 'sys_mcp_demo', 'room_name': 'rm_mcp_demo',
                'layer_name': 'Controllers', 'instance_name': 'inst_mcp_demo'}, True)
            await call('set_start_room', {'room_name': 'rm_mcp_demo'}, True)
            await call('move_resource', {'name': 'Room1', 'group': '房间'}, True)
            await call('inspect_resource', {'name': 'sys_mcp_demo'})
            await call('list_backups', {})
            checked = await call('inspect_project', {})
            assert checked['ok'], checked
    report.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'ok': True, 'report': str(report), 'resources': len(checked['assets']),
                      'ide_validation_required': True}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(run(args.project.resolve(strict=True), args.report.resolve()))
