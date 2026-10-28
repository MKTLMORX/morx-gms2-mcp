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
mcp_write_pending = [];
mcp_write_failures = 0;
mcp_write_stats = {};
mcp_io_snapshot = function() {
    var files = [];
    var retry_total = 0;
    var recovery_total = 0;
    var stalled_total = 0;
    var known_names = ["ready.json","response.json","errors.json"];
    for (var di=0;di<array_length(known_names);di++) {
        var diagnostic_name = known_names[di];
        if (!variable_struct_exists(mcp_write_stats,diagnostic_name)) continue;
        var diagnostic = variable_struct_get(mcp_write_stats,diagnostic_name);
        var pending_age = diagnostic.pending_since_ms < 0 ? 0 : max(0,current_time-diagnostic.pending_since_ms);
        var pending_now = array_contains(mcp_write_pending,diagnostic_name);
        var state_name = pending_now ? (pending_age>=10000 ? "stalled" : "retrying")
            : (diagnostic.recovery_count>0 ? "recovered" : "healthy");
        if (pending_now && pending_age>=10000) stalled_total++;
        retry_total += diagnostic.retry_attempts;
        recovery_total += diagnostic.recovery_count;
        array_push(files,{file:diagnostic_name,state:state_name,pending:pending_now,
            pending_age_ms:pending_age,retry_attempts:diagnostic.retry_attempts,
            current_retry_attempts:diagnostic.current_retry_attempts,recovery_count:diagnostic.recovery_count,
            last_recovery_attempts:diagnostic.last_recovery_attempts,
            first_failure_frame:diagnostic.first_failure_frame,last_failure_frame:diagnostic.last_failure_frame,
            last_recovery_frame:diagnostic.last_recovery_frame,last_failure_stage:diagnostic.last_failure_stage,
            request:diagnostic.request,last_failure_request:diagnostic.last_failure_request,
            last_recovery_request:diagnostic.last_recovery_request});
    }
    return {state:stalled_total>0 ? "stalled" : (array_length(mcp_write_pending)>0 ? "retrying"
            : (retry_total>0 ? "recovered" : "healthy")),
        pending_writes:array_length(mcp_write_pending),stalled_writes:stalled_total,
        retry_attempts:retry_total,retry_recoveries:recovery_total,
        write_failures:retry_total,write_failures_semantics:"retry_attempts_not_final_failures",
        sample_frame:mcp_frame,stall_threshold_ms:10000,files:files};
};
mcp_last_mouse_press = undefined;
mcp_last_mouse_release = undefined;
mcp_mouse_trace = undefined;
mcp_mouse_sample = function(_button) {
    return {frame:mcp_frame,window_x:window_mouse_get_x(),window_y:window_mouse_get_y(),
        gui_x:device_mouse_x_to_gui(0),gui_y:device_mouse_y_to_gui(0),
        down:mouse_check_button(_button),pressed:mouse_check_button_pressed(_button),
        released:mouse_check_button_released(_button)};
};
mcp_sample_mouse_trace = function() {
    if (is_undefined(mcp_mouse_trace) || !mcp_mouse_trace.active) return;
    var trace_sample = mcp_mouse_sample(mcp_mouse_trace.button_code);
    trace_sample.elapsed_ms = max(0,current_time-mcp_mouse_trace.started_ms);
    if (array_length(mcp_mouse_trace.samples)>=128) {
        // 保留首帧及最近 127 帧；按下/释放另存，不因截断丢失。
        array_delete(mcp_mouse_trace.samples,1,1);
        mcp_mouse_trace.dropped_samples++;
    }
    array_push(mcp_mouse_trace.samples,trace_sample);
    if (trace_sample.pressed) mcp_mouse_trace.press = trace_sample;
    if (trace_sample.released) mcp_mouse_trace.release = trace_sample;
    if (trace_sample.elapsed_ms>=10000) {
        mcp_mouse_trace.active = false;
        mcp_mouse_trace.expired = true;
        mcp_mouse_trace.end_frame = mcp_frame;
    }
};
mcp_flush_writes = function() {
    var remaining = [];
    for (var wi=0;wi<array_length(mcp_write_pending);wi++) {
        var write_name = mcp_write_pending[wi];
        var write_temp = mcp_directory+write_name+".tmp";
        var write_dest = mcp_directory+write_name;
        var write_stat = variable_struct_get(mcp_write_stats,write_name);
        if (!file_exists(write_temp)) {
            // 保留诊断，不把临时文件缺失误报为已经恢复；不重新执行原命令。
            write_stat.last_failure_stage = "temporary_file_missing";
            array_push(remaining,write_name);
            continue;
        }
        if (file_exists(write_dest)) file_delete(write_dest);
        if (!file_rename(write_temp,write_dest)) {
            array_push(remaining,write_name);
            mcp_write_failures++;
            write_stat.retry_attempts++;
            write_stat.current_retry_attempts++;
            if (write_stat.first_failure_frame<0) write_stat.first_failure_frame=mcp_frame;
            write_stat.last_failure_frame=mcp_frame;
            write_stat.last_failure_request=write_stat.request;
            write_stat.last_failure_stage=file_exists(write_dest) ? "replace_destination" : "promote_temporary";
        } else {
            if (write_stat.current_retry_attempts>0) {
                write_stat.recovery_count++;
                write_stat.last_recovery_attempts=write_stat.current_retry_attempts;
                write_stat.last_recovery_frame=mcp_frame;
                write_stat.last_recovery_request=write_stat.request;
            }
            write_stat.current_retry_attempts=0;
            write_stat.pending_since_ms=-1;
        }
    }
    mcp_write_pending = remaining;
};
mcp_write = function(_name,_value) {
    if (!variable_struct_exists(mcp_write_stats,_name)) {
        variable_struct_set(mcp_write_stats,_name,{retry_attempts:0,current_retry_attempts:0,recovery_count:0,
            last_recovery_attempts:0,pending_since_ms:-1,first_failure_frame:-1,last_failure_frame:-1,
            last_recovery_frame:-1,last_failure_stage:"",request:"",last_failure_request:"",last_recovery_request:""});
    }
    var write_stat = variable_struct_get(mcp_write_stats,_name);
    if (!array_contains(mcp_write_pending,_name)) write_stat.pending_since_ms=current_time;
    if (_name=="response.json" && variable_struct_exists(_value,"request")) {
        write_stat.request=string_copy(string(_value.request),1,32);
    }
    var temp_name = mcp_directory + _name + ".tmp";
    var dest_name = mcp_directory + _name;
    var h = file_text_open_write(temp_name);
    file_text_write_string(h,json_stringify(_value)); file_text_close(h);
    if (!array_contains(mcp_write_pending,_name)) array_push(mcp_write_pending,_name);
    mcp_flush_writes();
};
mcp_reply = function(_request,_ok,_data) {
    mcp_write("response.json",{session:mcp_session.session,request:_request,
        ok:_ok,data:_data,frame:mcp_frame});
};
mcp_release = function() {
    for (var k=0;k<array_length(mcp_keys);k++) keyboard_key_release(mcp_keys[k]);
    mcp_keys = [];
};
mcp_value = function(_value,_depth,_budget) {
    var depth_limit = variable_struct_exists(_budget,"depth") ? _budget.depth : 3;
    if (_budget.left <= 0) return {type:"truncated"};
    _budget.left -= 1;
    if (is_undefined(_value)) return {type:"undefined"};
    if (is_bool(_value)) return _value;
    if (is_int64(_value)) {
        if (_value > int64(9007199254740991) || _value < int64(-9007199254740991))
            return {type:"int64",value:string(_value)};
        return real(_value);
    }
    if (is_real(_value)) {
        if (is_nan(_value) || is_infinity(_value)) return {type:"non_finite"};
        return _value;
    }
    if (is_string(_value)) return string_copy(_value,1,1024);
    if (is_array(_value)) {
        var array_length_value = array_length(_value);
        if (_depth >= depth_limit) return {type:"array",length:array_length_value,items:[],truncated:true};
        var items = [];
        var array_take = min(array_length_value,32);
        for (var ai=0;ai<array_take;ai++) {
            array_push(items,mcp_value(_value[ai],_depth+1,_budget));
            if (_budget.left <= 0) break;
        }
        return {type:"array",length:array_length_value,items:items,
            truncated:(array_length(items)<array_length_value || _budget.left<=0)};
    }
    if (is_struct(_value)) {
        var names = variable_struct_get_names(_value);
        var struct_length_value = array_length(names);
        if (_depth >= depth_limit) return {type:"struct",length:struct_length_value,values:{},truncated:true};
        var values = {};
        var struct_take = min(struct_length_value,32);
        var struct_count = 0;
        for (var si=0;si<struct_take;si++) {
            var struct_field = names[si];
            variable_struct_set(values,struct_field,
                mcp_value(variable_struct_get(_value,struct_field),_depth+1,_budget));
            struct_count += 1;
            if (_budget.left <= 0) break;
        }
        return {type:"struct",length:struct_length_value,values:values,
            truncated:(struct_count<struct_length_value || _budget.left<=0)};
    }
    return {type:"complex"};
};
mcp_snapshot = function(_args) {
    var read_depth = variable_struct_exists(_args,"max_depth") ? clamp(_args.max_depth,1,8) : 3;
    var scope = "instances";
    if (variable_struct_exists(_args,"scope")) scope = _args.scope;
    var base = {room:room_get_name(room),room_width:room_width,room_height:room_height,
        window_width:window_get_width(),window_height:window_get_height(),
        gui_width:display_get_gui_width(),gui_height:display_get_gui_height(),
        frame:mcp_frame,fps:fps_real,scope:scope,
        mouse:{window_x:window_mouse_get_x(),window_y:window_mouse_get_y(),
            gui_x:device_mouse_x_to_gui(0),gui_y:device_mouse_y_to_gui(0),
            last_press:mcp_last_mouse_press,last_release:mcp_last_mouse_release,trace:mcp_mouse_trace},
        bridge_io:mcp_io_snapshot()};
    if (scope == "globals") {
        var globals = {};
        for (var gj=0;gj<array_length(_args.variables);gj++) {
            var global_field = _args.variables[gj];
            if (variable_global_exists(global_field)) {
                variable_struct_set(globals,global_field,
                    mcp_value(variable_global_get(global_field),0,{left:64,depth:read_depth}));
            } else variable_struct_set(globals,global_field,{type:"missing"});
        }
        base.globals = globals;
        base.truncated = false;
        return base;
    }
    var rows = [];
    var source = all;
    if (_args.object_name != "") {
        source = asset_get_index(_args.object_name);
        if (source == -1) return {error:"Object not found in running build"};
    }
    var single = variable_struct_exists(_args,"instance_id") && is_real(_args.instance_id);
    if (single) {
        var target_instance = _args.instance_id;
        if (!instance_exists(target_instance)) return {error:"Instance not found in running build"};
        if (_args.object_name != "" && target_instance.object_index != source) {
            return {error:"Instance does not match object_name"};
        }
        var one_values = {};
        for (var sj=0;sj<array_length(_args.variables);sj++) {
            var one_field = _args.variables[sj];
            if (variable_instance_exists(target_instance,one_field)) {
                variable_struct_set(one_values,one_field,
                    mcp_value(variable_instance_get(target_instance,one_field),0,{left:64,depth:read_depth}));
            } else variable_struct_set(one_values,one_field,{type:"missing"});
        }
        base.instances = [{id:target_instance.id,
            object_name:object_get_name(target_instance.object_index),values:one_values}];
        base.truncated = false;
        return base;
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
                variable_struct_set(values,field,
                    mcp_value(variable_instance_get(inst,field),0,{left:64,depth:read_depth}));
            } else variable_struct_set(values,field,{type:"missing"});
        }
        array_push(rows,{id:inst.id,object_name:object_get_name(inst.object_index),values:values});
    }
    base.instances = rows;
    base.truncated = visible_total>array_length(rows);
    return base;
};
mcp_collect_targets = function(_args,_source) {
    var targets = [];
    if (variable_struct_exists(_args,"instance_id")) {
        var one = _args.instance_id;
        if (!instance_exists(one)) return {error:"Instance not found in running build"};
        if (_source != all && one.object_index != _source) {
            return {error:"Instance does not match object_name"};
        }
        array_push(targets,one);
        return {targets:targets};
    }
    if (_source == all) return {error:"object_name or instance_id required"};
    var total = instance_number(_source);
    var cap = min(total,_args.limit);
    for (var i=0;i<cap;i++) array_push(targets,instance_find(_source,i));
    return {targets:targets};
};
mcp_bridge_asset = asset_get_index("sys_morx_runtime");
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
mcp_write("ready.json",{session:mcp_session.session,bridge:mcp_session.bridge,frame:0,bridge_io:mcp_io_snapshot()});
'''.replace('__KEY__', key)
    begin = r'''
mcp_frame++;
mcp_flush_writes();
if (mouse_check_button_pressed(mb_left) || mouse_check_button_pressed(mb_right)
    || mouse_check_button_pressed(mb_middle)) {
    mcp_last_mouse_press = {frame:mcp_frame,window_x:window_mouse_get_x(),window_y:window_mouse_get_y(),
        gui_x:device_mouse_x_to_gui(0),gui_y:device_mouse_y_to_gui(0)};
}
if (mouse_check_button_released(mb_left) || mouse_check_button_released(mb_right)
    || mouse_check_button_released(mb_middle)) {
    mcp_last_mouse_release = {frame:mcp_frame,window_x:window_mouse_get_x(),window_y:window_mouse_get_y(),
        gui_x:device_mouse_x_to_gui(0),gui_y:device_mouse_y_to_gui(0)};
}
mcp_sample_mouse_trace();
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
    mcp_write("ready.json",{session:mcp_session.session,bridge:mcp_session.bridge,frame:mcp_frame,bridge_io:mcp_io_snapshot()});
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
        case "mouse_trace":
            if (request.args.phase == "begin") {
                var trace_button = request.args.button;
                var trace_button_code = trace_button == "right" ? mb_right
                    : (trace_button == "middle" ? mb_middle : mb_left);
                mcp_mouse_trace = {trace_id:request.args.trace_id,source:"BeginStep",
                    button:trace_button,button_code:trace_button_code,active:true,expired:false,
                    start_frame:mcp_frame,end_frame:mcp_frame,started_ms:current_time,
                    capacity:128,dropped_samples:0,samples:[],press:undefined,release:undefined};
                mcp_sample_mouse_trace();
            } else if (request.args.phase == "end" && !is_undefined(mcp_mouse_trace)
                && mcp_mouse_trace.trace_id == request.args.trace_id) {
                mcp_mouse_trace.active = false;
                mcp_mouse_trace.end_frame = mcp_frame;
            } else {
                mcp_reply(request.request,false,{error:"Mouse trace does not match"}); break;
            }
            mcp_reply(request.request,true,mcp_mouse_trace);
            break;
        case "state":
            mcp_reply(request.request,true,mcp_snapshot(request.args));
            break;
        case "audio":
            var audio_args = request.args;
            var audio_result = {sounds:{},voices:{},groups:{},system_available:audio_system_is_available()};
            for (var audio_i=0;audio_i<min(32,array_length(audio_args.sound_names));audio_i++) {
                var audio_name = audio_args.sound_names[audio_i];
                var audio_asset = asset_get_index(audio_name);
                var audio_row = {exists:false};
                try {
                    if (audio_asset != -1 && asset_get_type(audio_asset)==asset_sound && audio_exists(audio_asset)) {
                        var audio_group = audio_sound_get_audio_group(audio_asset);
                        var audio_group_label = audio_group_name(audio_group);
                        audio_row = {exists:true,playing:audio_is_playing(audio_asset),paused:audio_is_paused(audio_asset),
                            audio_group:audio_group_label,type:audio_get_type(audio_asset),
                            asset_gain:audio_sound_get_gain(audio_asset),asset_pitch:audio_sound_get_pitch(audio_asset),
                            start_offset_seconds:audio_sound_get_track_position(audio_asset),length_seconds:audio_sound_length(audio_asset)};
                        variable_struct_set(audio_result.groups,audio_group_label,
                            {loaded:audio_group_is_loaded(audio_group),gain:audio_group_get_gain(audio_group)});
                    }
                } catch (audio_error) { audio_row = {error:mcp_text(audio_error.message,256)}; }
                variable_struct_set(audio_result.sounds,audio_name,audio_row);
            }
            for (var voice_i=0;voice_i<min(16,array_length(audio_args.voices));voice_i++) {
                var voice_spec = audio_args.voices[voice_i];
                var voice_value = undefined;
                var voice_row = {status:"missing"};
                try {
                    if (voice_spec.scope=="globals") {
                        if (variable_global_exists(voice_spec.variable)) voice_value=variable_global_get(voice_spec.variable);
                    } else if (instance_exists(voice_spec.instance_id)) {
                        if (variable_instance_exists(voice_spec.instance_id,voice_spec.variable))
                            voice_value=variable_instance_get(voice_spec.instance_id,voice_spec.variable);
                    }
                    if (!is_undefined(voice_value)) {
                        if ((!is_real(voice_value) && !is_int64(voice_value)) || is_bool(voice_value)) {
                            voice_row={status:"unsupported",error:"Handle variable must contain a sound instance ID"};
                        } else {
                            var voice_playing=audio_is_playing(voice_value);
                            var voice_paused=audio_is_paused(voice_value);
                            voice_row={status:"inactive",playing:voice_playing,paused:voice_paused};
                            if (voice_playing || voice_paused) {
                                voice_row.status="active";
                                voice_row.asset=audio_get_name(audio_sound_get_asset(voice_value));
                                voice_row.position_seconds=audio_sound_get_track_position(voice_value);
                                voice_row.instance_gain=audio_sound_get_gain(voice_value);
                                voice_row.instance_pitch=audio_sound_get_pitch(voice_value);
                            }
                        }
                    }
                } catch (voice_error) { voice_row={status:"error",error:mcp_text(voice_error.message,256)}; }
                variable_struct_set(audio_result.voices,voice_spec.label,voice_row);
            }
            mcp_reply(request.request,true,audio_result);
            break;
        case "set":
            var set_scope = request.args.scope;
            var set_values = request.args.values;
            if (!is_struct(set_values)) {
                mcp_reply(request.request,false,{error:"values must be a struct"}); break;
            }
            var set_names = variable_struct_get_names(set_values);
            if (array_length(set_names)<1 || array_length(set_names)>16) {
                mcp_reply(request.request,false,{error:"values must contain 1 to 16 variables"}); break;
            }
            var set_create = false;
            if (variable_struct_exists(request.args,"create_missing")) set_create = request.args.create_missing;
            if (set_scope == "globals") {
                var set_global_error = "";
                for (var sg=0;sg<array_length(set_names);sg++) {
                    var set_gname = set_names[sg];
                    if (set_gname == "id" || set_gname == "object_index") {
                        set_global_error = "Protected variable: "+set_gname;
                    } else if (!variable_global_exists(set_gname) && !set_create) {
                        set_global_error = "Global variable not found: "+set_gname;
                    }
                    if (set_global_error != "") break;
                }
                if (set_global_error != "") {
                    mcp_reply(request.request,false,{error:set_global_error}); break;
                }
                var set_global_previous = {};
                var set_global_created = [];
                for (var sw=0;sw<array_length(set_names);sw++) {
                    var set_w_name = set_names[sw];
                    if (variable_global_exists(set_w_name)) {
                        variable_struct_set(set_global_previous,set_w_name,
                            mcp_value(variable_global_get(set_w_name),0,{left:64}));
                    } else {
                        variable_struct_set(set_global_previous,set_w_name,{type:"missing"});
                        array_push(set_global_created,set_w_name);
                    }
                    variable_global_set(set_w_name,
                        variable_clone(variable_struct_get(set_values,set_w_name)));
                }
                mcp_reply(request.request,true,{scope:"globals",previous:set_global_previous,
                    created:set_global_created,count:array_length(set_names),frame:mcp_frame});
                break;
            }
            if (set_scope != "instances") {
                mcp_reply(request.request,false,{error:"Invalid scope"}); break;
            }
            var set_source = all;
            if (request.args.object_name != "") {
                set_source = asset_get_index(request.args.object_name);
                if (set_source == -1) {
                    mcp_reply(request.request,false,{error:"Object not found in running build"}); break;
                }
                if (set_source == mcp_bridge_asset) {
                    mcp_reply(request.request,false,{error:"Managed bridge cannot be modified"}); break;
                }
            }
            var set_collected = mcp_collect_targets(request.args,set_source);
            if (variable_struct_exists(set_collected,"error")) {
                mcp_reply(request.request,false,{error:set_collected.error}); break;
            }
            var set_targets = set_collected.targets;
            if (array_length(set_targets)<1) {
                mcp_reply(request.request,false,{error:"No matching instances"}); break;
            }
            var set_target_error = "";
            for (var st=0;st<array_length(set_targets);st++) {
                var set_target = set_targets[st];
                if (set_target == id || set_target.object_index == mcp_bridge_asset) {
                    set_target_error = "Managed bridge cannot be modified";
                    break;
                }
                for (var sn=0;sn<array_length(set_names);sn++) {
                    var set_name = set_names[sn];
                    if (set_name == "id" || set_name == "object_index") {
                        set_target_error = "Protected variable: "+set_name;
                    } else if (!variable_instance_exists(set_target,set_name) && !set_create) {
                        set_target_error = "Instance variable not found: "+set_name;
                    }
                    if (set_target_error != "") break;
                }
                if (set_target_error != "") break;
            }
            if (set_target_error != "") {
                mcp_reply(request.request,false,{error:set_target_error}); break;
            }
            var set_rows = [];
            for (var sr=0;sr<array_length(set_targets);sr++) {
                var set_row_target = set_targets[sr];
                var set_previous = {};
                for (var sv=0;sv<array_length(set_names);sv++) {
                    var set_v_name = set_names[sv];
                    if (variable_instance_exists(set_row_target,set_v_name)) {
                        variable_struct_set(set_previous,set_v_name,
                            mcp_value(variable_instance_get(set_row_target,set_v_name),0,{left:64}));
                    } else variable_struct_set(set_previous,set_v_name,{type:"missing"});
                    variable_instance_set(set_row_target,set_v_name,
                        variable_clone(variable_struct_get(set_values,set_v_name)));
                }
                array_push(set_rows,{id:set_row_target.id,
                    object_name:object_get_name(set_row_target.object_index),previous:set_previous});
            }
            mcp_reply(request.request,true,{scope:"instances",instances:set_rows,
                count:array_length(set_names),frame:mcp_frame});
            break;
        case "spawn":
            var spawn_source = asset_get_index(request.args.object_name);
            if (spawn_source == -1) {
                mcp_reply(request.request,false,{error:"Object not found in running build"}); break;
            }
            if (spawn_source == mcp_bridge_asset) {
                mcp_reply(request.request,false,{error:"Managed bridge cannot be spawned"}); break;
            }
            var spawn_instance = noone;
            if (request.args.layer_name != "") {
                if (!layer_exists(request.args.layer_name)) {
                    mcp_reply(request.request,false,{error:"Layer not found in running room"}); break;
                }
                spawn_instance = instance_create_layer(request.args.x,request.args.y,
                    request.args.layer_name,spawn_source);
            } else {
                spawn_instance = instance_create_depth(request.args.x,request.args.y,
                    request.args.depth,spawn_source);
            }
            if (!instance_exists(spawn_instance)) {
                mcp_reply(request.request,false,{error:"Instance creation failed"}); break;
            }
            mcp_reply(request.request,true,{instance:{id:spawn_instance.id,
                object_name:object_get_name(spawn_instance.object_index),
                x:spawn_instance.x,y:spawn_instance.y},frame:mcp_frame});
            break;
        case "destroy":
            var destroy_source = all;
            if (request.args.object_name != "") {
                destroy_source = asset_get_index(request.args.object_name);
                if (destroy_source == -1) {
                    mcp_reply(request.request,false,{error:"Object not found in running build"}); break;
                }
                if (destroy_source == mcp_bridge_asset) {
                    mcp_reply(request.request,false,{error:"Managed bridge cannot be destroyed"}); break;
                }
            }
            var destroy_collected = mcp_collect_targets(request.args,destroy_source);
            if (variable_struct_exists(destroy_collected,"error")) {
                mcp_reply(request.request,false,{error:destroy_collected.error}); break;
            }
            var destroy_targets = destroy_collected.targets;
            var destroy_rows = [];
            var destroy_skipped = 0;
            for (var dt=0;dt<array_length(destroy_targets);dt++) {
                var destroy_target = destroy_targets[dt];
                if (destroy_target == id || !instance_exists(destroy_target)
                    || destroy_target.object_index == mcp_bridge_asset) {
                    destroy_skipped++;
                    continue;
                }
                array_push(destroy_rows,{id:destroy_target.id,
                    object_name:object_get_name(destroy_target.object_index)});
                instance_destroy(destroy_target);
            }
            mcp_reply(request.request,true,{destroyed:destroy_rows,count:array_length(destroy_rows),
                skipped:destroy_skipped,frame:mcp_frame});
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
                var allowed = [8,9,13,27,32,33,34,35,36,37,38,39,40,45,46];
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
