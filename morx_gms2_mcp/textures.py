"""普通纹理组的限定编辑；原生字段与资源分组是两种独立关系。"""
from .display import integer, boolean


FIELDS = {'mips_to_generate': 'mipsToGenerate', 'border': 'border',
          'autocrop': 'autocrop', 'allow_scaling': 'isScaled'}


def groups(meta):
    rows = meta.get('TextureGroups')
    if not isinstance(rows, list) or not rows:
        raise ValueError('项目缺少 TextureGroups')
    names = set()
    for row in rows:
        name = row.get('name') if isinstance(row, dict) else None
        if not isinstance(name, str) or not name or name.casefold() in names:
            raise ValueError('纹理组名称为空或重复')
        names.add(name.casefold())
    if sum(row['name'] == 'Default' for row in rows) != 1:
        raise ValueError('项目必须有唯一 Default 纹理组')
    return rows


def editable(meta, row):
    if (row.get('$GMTextureGroup') != '' or row.get('resourceType') != 'GMTextureGroup'
            or row.get('resourceVersion') != '2.0' or row.get('%Name') != row['name']):
        raise ValueError('仅编辑 GMTextureGroup 2.0，不自动迁移格式')
    if meta.get('ConfigValues') or row.get('ConfigValues'):
        raise ValueError('纹理组或项目含配置覆盖，暂不修改')
    if row.get('groupParent') is not None or any(
            isinstance(g.get('groupParent'), dict) and g['groupParent'].get('name') == row['name']
            for g in groups(meta)):
        raise ValueError('父子纹理组暂不修改，请使用独立纹理组')
    if (row.get('loadType') != 'default' or row.get('targets') != -1
            or type(row.get('targets')) is not int or row.get('directory') != ''
            or row.get('compressFormat') not in ('bz2', 'qoi', 'png') or row.get('customOptions') != ''):
        raise ValueError('仅编辑覆盖全部平台的普通纹理组，不修改动态组或自定义压缩')
    integer(row.get('mipsToGenerate'), 'mips_to_generate', 0, 4)
    integer(row.get('border'), 'border', 0, 64)
    boolean(row.get('autocrop'), 'autocrop')
    boolean(row.get('isScaled'), 'allow_scaling')


def settings(row):
    return {name: row.get(field) for name, field in FIELDS.items()}


def update(row, mips_to_generate=None, border=None, autocrop=None, allow_scaling=None):
    for name, value in (('mips_to_generate', mips_to_generate), ('border', border),
                        ('autocrop', autocrop), ('allow_scaling', allow_scaling)):
        if value is None:
            continue
        if name in ('autocrop', 'allow_scaling'):
            boolean(value, name)
        else:
            integer(value, name, 0, 4 if name == 'mips_to_generate' else 64)
        row[FIELDS[name]] = value
    if row['mipsToGenerate'] and row['border'] < 8:
        raise ValueError('生成 Mipmap 时 border 至少为 8；请显式指定，预览后执行')


def new_group(name):
    return {'$GMTextureGroup': '', '%Name': name, 'name': name,
            'resourceType': 'GMTextureGroup', 'resourceVersion': '2.0',
            'mipsToGenerate': 0, 'border': 2, 'autocrop': True, 'isScaled': True,
            'compressFormat': 'bz2', 'customOptions': '', 'directory': '',
            'groupParent': None, 'loadType': 'default', 'targets': -1}


def notes(row):
    result = ['纹理组不等于资源树分组；分组不自动切换运行时过滤方式',
              '参数不保证无锯齿或串色；需按实际缩放比例对照画面']
    if row.get('mipsToGenerate', 0):
        result.append('Mipmap 增加纹理存储；留意像素画、字体和透明边缘的采样效果')
        if type(row.get('border')) is int and row['border'] < 8:
            result.append('当前 Mipmap 边距小于 8，存在相邻图像串色风险')
    return result
