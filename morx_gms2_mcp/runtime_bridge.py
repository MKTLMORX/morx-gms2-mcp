"""生成项目内测试桥接；只在带会话文件的隔离运行副本中启用。"""
import re

OBJECT = 'sys_morx_runtime'
LAYER = 'MORX_Runtime'
MARKER = '// MORX_RUNTIME_BRIDGE 1 '


def bridge_key(code):
    match = re.match(re.escape(MARKER) + r'([a-f0-9]{32})\r?\n', code)
    if not match:
        raise ValueError('测试桥接标记不匹配；不覆盖同名资源')
    return match[1]


def sources(key):
    if not re.fullmatch('[a-f0-9]{32}', key):
        raise ValueError('无效桥接标识')
    create = MARKER + key + '\n' + r'''
mcp_directory = "morx_mcp/";
mcp_session = undefined;
mcp_read_json = function(_name) {
    var _path = mcp_directory + _name;
    if (!file_exists(_path)) return undefined;
    var _h = file_text_open_read(_path);
    var _raw = "";
    while (!file_text_eof(_h)) {
        _raw += file_text_read_string(_h);
        if (!file_text_eof(_h)) file_text_readln(_h);
    }
    file_text_close(_h);
    return json_parse(_raw);
};
try { mcp_session = mcp_read_json("session.json"); } catch (err) { instance_destroy(); exit; }
if (is_undefined(mcp_session)) { instance_destroy(); exit; }
if (!is_struct(mcp_session)) { instance_destroy(); exit; }
if (!variable_struct_exists(mcp_session,"bridge") || !variable_struct_exists(mcp_session,"session")) {
    instance_destroy(); exit;
}
if (mcp_session.bridge != "__KEY__") { instance_destroy(); exit; }
persistent = true;
depth = -1000000000;
mcp_frame = 0;
mcp_last_request = "";
mcp_pending = undefined;
mcp_capture = undefined;
mcp_keys = [];
mcp_until = 0;
mcp_started = current_time;
mcp_write = function(_name,_value) {
    var temp_name = mcp_directory + _name + ".tmp";
    var dest_name = mcp_directory + _name;
    var h = file_text_open_write(temp_name);
    file_text_write_string(h,json_stringify(_value)); file_text_close(h);
    if (file_exists(dest_name)) file_delete(dest_name);
    file_rename(temp_name,dest_name);
};
mcp_reply = function(_request,_ok,_data) {
    mcp_write("response.json",{session:mcp_session.session,request:_request,
        ok:_ok,data:_data,frame:mcp_frame});
};
mcp_release = function() {
    for (var k=0;k<array_length(mcp_keys);k++) keyboard_key_release(mcp_keys[k]);
    mcp_keys = [];
};
mcp_value = function(_value) {
    if (is_undefined(_value)) return {type:"undefined"};
    if (is_bool(_value)) return _value;
    if (is_real(_value)) {
        if (is_nan(_value) || is_infinity(_value)) return {type:"non_finite"};
        return _value;
    }
    if (is_string(_value)) return string_copy(_value,1,1024);
    return {type:"complex"};
};
mcp_snapshot = function(_args) {
    var rows = [];
    var source = all;
    if (_args.object_name != "") {
        source = asset_get_index(_args.object_name);
        if (source == -1) return {error:"Object not found in running build"};
    }
    var total = instance_number(source);
    var visible_total = total;
    if (_args.object_name == "") visible_total -= 1;
    for (var i=0;i<total && array_length(rows)<_args.limit;i++) {
        var inst = instance_find(source,i);
        if (inst == id) continue;
        var values = {};
        for (var j=0;j<array_length(_args.variables);j++) {
            var field = _args.variables[j];
            if (variable_instance_exists(inst,field)) {
                variable_struct_set(values,field,mcp_value(variable_instance_get(inst,field)));
            } else variable_struct_set(values,field,{type:"missing"});
        }
        array_push(rows,{id:inst.id,object_name:object_get_name(inst.object_index),values:values});
    }
    return {room:room_get_name(room),room_width:room_width,room_height:room_height,
        window_width:window_get_width(),window_height:window_get_height(),
        gui_width:display_get_gui_width(),gui_height:display_get_gui_height(),
        frame:mcp_frame,fps:fps_real,instances:rows,truncated:visible_total>array_length(rows)};
};
mcp_errors = [];
mcp_text = function(_value,_limit) {
    if (is_undefined(_value)) return "";
    return string_copy(string(_value),1,_limit);
};
mcp_record_error = function(_stage,_message,_long,_script,_line,_stack) {
    var item = {frame:mcp_frame,stage:_stage,message:mcp_text(_message,2048),
        long_message:mcp_text(_long,4096),script:mcp_text(_script,1024),
        line:is_real(_line)?_line:-1,stacktrace:[]};
    if (is_array(_stack)) {
        for (var i=0;i<array_length(_stack) && i<32;i++) {
            array_push(item.stacktrace,mcp_text(_stack[i],1024));
        }
    }
    array_push(mcp_errors,item);
    if (array_length(mcp_errors)>20) array_delete(mcp_errors,0,array_length(mcp_errors)-20);
    try { mcp_write("errors.json",{session:mcp_session.session,errors:mcp_errors}); } catch (err) {}
};
mcp_previous_error_handler = undefined;
try {
    mcp_previous_error_handler = exception_unhandled_handler(function(_exception) {
        mcp_record_error("unhandled",_exception.message,_exception.longMessage,
            _exception.script,_exception.line,_exception.stacktrace);
    });
} catch (err) {}
mcp_write("ready.json",{session:mcp_session.session,bridge:mcp_session.bridge,frame:0});
'''.replace('__KEY__', key)
    begin = r'''
mcp_frame++;
if (current_time-mcp_started > 900000 || ((mcp_frame mod 5) == 0 && !file_exists(mcp_directory+"session.json"))) {
    mcp_release(); game_end(); exit;
}
if (array_length(mcp_keys)>0 && !window_has_focus()) {
    mcp_release();
    if (!is_undefined(mcp_pending)) mcp_reply(mcp_pending.request,false,{error:"Game lost focus; input released"});
    mcp_pending = undefined;
}
if (!is_undefined(mcp_pending) && mcp_frame>=mcp_until) {
    mcp_release();
    mcp_reply(mcp_pending.request,true,{frames:mcp_pending.frames});
    mcp_pending = undefined;
}
if ((mcp_frame mod 60) == 0) {
    mcp_write("ready.json",{session:mcp_session.session,bridge:mcp_session.bridge,frame:mcp_frame});
}
if (!file_exists(mcp_directory+"request.json")) exit;
var request;
try { request = mcp_read_json("request.json"); } catch (err) { exit; }
if (is_undefined(request) || !is_struct(request)) exit;
if (!variable_struct_exists(request,"session") || !variable_struct_exists(request,"request")) exit;
if (request.session != mcp_session.session || request.request == mcp_last_request) exit;
mcp_last_request = request.request;
file_delete(mcp_directory+"request.json");
try {
    if (!is_undefined(mcp_pending) || !is_undefined(mcp_capture)) {
        mcp_reply(request.request,false,{error:"Another command is pending"}); exit;
    }
    switch (request.command) {
        case "state":
            mcp_reply(request.request,true,mcp_snapshot(request.args));
            break;
        case "capture":
            mcp_capture = request.request;
            break;
        case "wait":
        case "input":
            if (request.args.frames<1 || request.args.frames>600) {
                mcp_reply(request.request,false,{error:"Invalid frame count"}); break;
            }
            if (request.command == "input") {
                if (!window_has_focus()) {
                    mcp_reply(request.request,false,{error:"Game must have focus for keyboard input"}); break;
                }
                var allowed = [8,9,13,27,32,37,38,39,40];
                var valid = true;
                if (array_length(request.args.keys)>8) valid = false;
                for (var i=0;i<array_length(request.args.keys);i++) {
                    var key = request.args.keys[i];
                    if (!((key>=48 && key<=57) || (key>=65 && key<=90)
                        || array_contains(allowed,key))) valid = false;
                }
                if (!valid) { mcp_reply(request.request,false,{error:"Unsupported key"}); break; }
                mcp_keys = request.args.keys;
                for (var i=0;i<array_length(mcp_keys);i++) keyboard_key_press(mcp_keys[i]);
            }
            mcp_pending = {request:request.request,frames:request.args.frames};
            mcp_until = mcp_frame + request.args.frames;
            break;
        case "event":
            var ev_name = request.args.object_name;
            var ev_source = asset_get_index(ev_name);
            if (ev_source == -1) {
                mcp_reply(request.request,false,{error:"Object not found in running build"}); break;
            }
            var ev_type = request.args.event_type;
            var ev_number = request.args.event_number;
            if (!is_real(ev_type) || !is_real(ev_number)) {
                mcp_reply(request.request,false,{error:"Invalid event"}); break;
            }
            if (ev_type == 4) {
                if (!variable_struct_exists(request.args,"collision_object")) {
                    mcp_reply(request.request,false,{error:"Collision target required"}); break;
                }
                ev_number = asset_get_index(request.args.collision_object);
                if (ev_number == -1) {
                    mcp_reply(request.request,false,{error:"Collision object not found"}); break;
                }
            }
            var ev_targets = [];
            if (variable_struct_exists(request.args,"instance_id")) {
                var ev_instance = request.args.instance_id;
                if (!instance_exists(ev_instance)) {
                    mcp_reply(request.request,false,{error:"Instance not found"}); break;
                }
                if (ev_instance.object_index != ev_source) {
                    mcp_reply(request.request,false,{error:"Instance does not match object"}); break;
                }
                array_push(ev_targets,ev_instance);
            } else {
                var ev_total = instance_number(ev_source);
                if (ev_total<1) {
                    mcp_reply(request.request,false,{error:"No instances of object"}); break;
                }
                var ev_cap = min(ev_total,request.args.limit);
                for (var ev_i=0;ev_i<ev_cap;ev_i++) array_push(ev_targets,instance_find(ev_source,ev_i));
            }
            var ev_performed = 0;
            for (var ev_j=0;ev_j<array_length(ev_targets);ev_j++) {
                var ev_target = ev_targets[ev_j];
                if (!instance_exists(ev_target)) continue;
                with (ev_target) {
                    event_perform_object(ev_source,ev_type,ev_number);
                }
                ev_performed++;
            }
            mcp_reply(request.request,true,{object_name:ev_name,event:request.args.event,
                event_type:ev_type,event_number:ev_number,instances:ev_performed,frame:mcp_frame});
            break;
        default: mcp_reply(request.request,false,{error:"Unsupported command"});
    }
} catch (err) {
    mcp_release();
    mcp_record_error("bridge",err.message,err.longMessage,err.script,err.line,err.stacktrace);
    mcp_reply(request.request,false,{error:"Bridge command failed: "+mcp_text(err.message,512)});
}
'''
    draw = r'''
if (!is_undefined(mcp_capture)) {
    try {
        screen_save(mcp_directory+"capture_"+mcp_capture+".png");
        mcp_reply(mcp_capture,true,{frame:mcp_frame});
    } catch (err) {
        mcp_record_error("capture",err.message,err.longMessage,err.script,err.line,err.stacktrace);
        mcp_reply(mcp_capture,false,{error:"Capture failed: "+mcp_text(err.message,512)});
    }
    mcp_capture = undefined;
}
'''
    cleanup = r'''
if (variable_instance_exists(id,"mcp_previous_error_handler")) {
    try { exception_unhandled_handler(mcp_previous_error_handler); } catch (err) {}
}
if (variable_instance_exists(id,"mcp_release")) mcp_release();
'''
    return {'Create': create, 'BeginStep': begin, 'DrawGUIEnd': draw, 'CleanUp': cleanup}
