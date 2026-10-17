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
mcp_value = function(_value,_depth,_budget) {
    if (_budget.left <= 0) return {type:"truncated"};
    _budget.left -= 1;
    if (is_undefined(_value)) return {type:"undefined"};
    if (is_bool(_value)) return _value;
    if (is_real(_value)) {
        if (is_nan(_value) || is_infinity(_value)) return {type:"non_finite"};
        return _value;
    }
    if (is_string(_value)) return string_copy(_value,1,1024);
    if (is_array(_value)) {
        var array_length_value = array_length(_value);
        if (_depth >= 3) return {type:"array",length:array_length_value,items:[],truncated:true};
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
        if (_depth >= 3) return {type:"struct",length:struct_length_value,values:{},truncated:true};
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
    var scope = "instances";
    if (variable_struct_exists(_args,"scope")) scope = _args.scope;
    var base = {room:room_get_name(room),room_width:room_width,room_height:room_height,
        window_width:window_get_width(),window_height:window_get_height(),
        gui_width:display_get_gui_width(),gui_height:display_get_gui_height(),
        frame:mcp_frame,fps:fps_real,scope:scope};
    if (scope == "globals") {
        var globals = {};
        for (var gj=0;gj<array_length(_args.variables);gj++) {
            var global_field = _args.variables[gj];
            if (variable_global_exists(global_field)) {
                variable_struct_set(globals,global_field,
                    mcp_value(variable_global_get(global_field),0,{left:64}));
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
                    mcp_value(variable_instance_get(target_instance,one_field),0,{left:64}));
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
                    mcp_value(variable_instance_get(inst,field),0,{left:64}));
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
