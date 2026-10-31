from copy import deepcopy
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import Field, StrictInt, create_model, model_validator
from sqlalchemy import select

from .core import Actor, DTO, DomainError, Envelope, actor_dep, advisory_lock, audit, lock, ok, require_admin, touch
from .db import get_db
from .models import ConfigSection

router = APIRouter()
DEFAULTS = {
    'prices': dict.fromkeys(('bwSingle','bwDuplex','colorSingle','colorDuplex','photo','corner','side')),
    'services': {'serviceEnabled':False,'maxFiles':10,'maxMb':50,'maxOrderMb':200,'maxPages':1000,'maxCopies':100,
        'maxPixels':40000000,'maxExpandedPages':10000,'maxFinalMb':500,'bindingApproved':True,
        'bindingLimits':{'CORNER':None,'SIDE':None},'formats':['pdf','doc','docx','ppt','pptx','xls','xlsx','jpg','jpeg','png','webp','heic'],
        'photoEnabled':False,'photoCalibrated':False},
    'photo': {'photoWidthMm':127,'photoHeightMm':89,'margins':{'top':3,'bottom':3,'left':3,'right':3}},
    'contact': {'wechat':'','phone':'','qrUrl':'','displayName':'DormPrint','guide':''},
    'announcement': {'announcement':'','serviceEnabled':False},
    'workflow': {'unpaidTimeoutMinutes':None,'quoteMinutes':15,'locationMaxAgeSeconds':300,'locationMaxAccuracyMeters':100,
        'shortCodeLength':5,'pauses':{'newOrders':False,'fileProcessing':False,'production':False,'delivery':False}},
    'retention': {'sourceDays':7,'finalDays':30,'proofDays':30,'draftHours':24,'exportHours':24,'trashHours':24},
    'map': {'mapUrl':'','mapVersion':1,'assetId':None},
    'volume-discounts': {'id':'volume-main','enabled':False,'description':'','effectiveAt':None,'tiers':[],'stackable':False},
    'mail': {'enabled':False,'host':None,'port':None,'securityMode':'STARTTLS','smtpAuthEnabled':True,'username':None,
        'fromName':None,'fromAddress':None,'replyTo':None,'connectionTimeoutSeconds':10,'configurationStatus':'UNCONFIGURED'},
}


def get_config(db, section):
    if section not in DEFAULTS:
        raise DomainError('UNKNOWN_CONFIG_SECTION', '配置分类不存在', 404)
    row = db.scalar(select(ConfigSection).where(ConfigSection.section == section))
    if not row:
        from sqlalchemy.dialects.postgresql import insert
        if db.bind.dialect.name=='postgresql':
            db.execute(insert(ConfigSection).values(section=section,data=deepcopy(DEFAULTS[section])).on_conflict_do_nothing(index_elements=['section']))
            row=db.scalar(select(ConfigSection).where(ConfigSection.section==section))
        else:
            row = ConfigSection(section=section, data=deepcopy(DEFAULTS[section]))
            db.add(row)
            db.flush()
    return row


def public_config(db):
    sections = {name:get_config(db,name) for name in ('prices','services','photo','contact','announcement','workflow','map')}
    result = {}
    for name in ('services','photo','contact','announcement','map'):
        result.update(sections[name].data)
    result['prices'] = sections['prices'].data.copy()
    result['versions'] = {name:row.version for name,row in sections.items()}
    result['version'] = max(row.version for row in sections.values())
    workflow = sections['workflow'].data
    result.update({'quoteMinutes':15,'unpaidTimeoutMinutes':workflow.get('unpaidTimeoutMinutes'),
                   'pauses':workflow.get('pauses',{}),'bindingApproved':True,
                   'locationMaxAgeSeconds':workflow.get('locationMaxAgeSeconds',300),
                   'locationMaxAccuracyMeters':workflow.get('locationMaxAccuracyMeters',100)})
    result['features'] = {'guestCheckoutEnabled':True,'customerEmailAuthAvailable':False,'promotionsEnabled':True,
        'resumableUploads':True,'serverDrafts':True,'afterSales':True,'selfDeletionRequest':True}
    try:
        from .mail import mail_available
        result['features']['customerEmailAuthAvailable'] = mail_available(db)
    except ImportError:
        pass
    result['decisions'] = {'D-01':'EXCLUDED','D-02':'APPROVED','D-03':'APPROVED','D-04':'APPROVED',
        'D-05':'APPROVED','D-06A':'APPROVED','D-06B':'APPROVED','D-06C':'EXCLUDED','D-07':'APPROVED','D-08':'APPROVED'}
    # Only delivery acceptance limits are public so clients mirror current validation; SMTP, security secrets and storage paths stay private.
    return result


class PricePatch(DTO):
    expected_version: StrictInt
    bw_single: StrictInt | None = Field(default=None, ge=0)
    bw_duplex: StrictInt | None = Field(default=None, ge=0)
    color_single: StrictInt | None = Field(default=None, ge=0)
    color_duplex: StrictInt | None = Field(default=None, ge=0)
    photo: StrictInt | None = Field(default=None, ge=0)
    corner: StrictInt | None = Field(default=None, ge=0)
    side: StrictInt | None = Field(default=None, ge=0)


class ServicesPatch(DTO):
    expected_version: StrictInt
    service_enabled: bool | None = None
    max_files: StrictInt | None = Field(default=None,ge=1,le=100)
    max_mb: StrictInt | None = Field(default=None,ge=1,le=200)
    max_order_mb: StrictInt | None = Field(default=None,ge=1,le=2000)
    max_pages: StrictInt | None = Field(default=None,ge=1,le=10000)
    max_copies: StrictInt | None = Field(default=None,ge=1,le=1000)
    max_pixels: StrictInt | None = Field(default=None,ge=1,le=100000000)
    max_expanded_pages: StrictInt | None = Field(default=None,ge=1,le=100000)
    max_final_mb: StrictInt | None = Field(default=None,ge=1,le=2000)
    binding_limits: dict[str, StrictInt | None] | None = None
    formats: list[Literal['pdf','doc','docx','ppt','pptx','xls','xlsx','jpg','jpeg','png','webp','heic']] | None = None
    photo_enabled: bool | None = None
    photo_calibrated: bool | None = None

    @model_validator(mode='after')
    def limits_valid(self):
        if self.binding_limits is not None:
            if set(self.binding_limits)-{'CORNER','SIDE'} or any(v is not None and v <= 0 for v in self.binding_limits.values()):
                raise ValueError('装订上限须为正整数')
        return self


class Margins(DTO):
    top: float = Field(ge=0,le=100)
    bottom: float = Field(ge=0,le=100)
    left: float = Field(ge=0,le=100)
    right: float = Field(ge=0,le=100)


class PhotoPatch(DTO):
    expected_version: StrictInt
    photo_width_mm: float | None = Field(default=None,gt=0,le=500)
    photo_height_mm: float | None = Field(default=None,gt=0,le=500)
    margins: Margins | None = None


class ContactPatch(DTO):
    expected_version: StrictInt
    wechat: str | None = Field(default=None,max_length=120)
    phone: str | None = Field(default=None,max_length=32)
    qr_url: str | None = None
    display_name: str | None = Field(default=None,max_length=120)
    guide: str | None = Field(default=None,max_length=1000)


class AnnouncementPatch(DTO):
    expected_version: StrictInt
    announcement: str | None = Field(default=None,max_length=3000)
    service_enabled: bool | None = None


class PauseConfig(DTO):
    new_orders: bool = False
    file_processing: bool = False
    production: bool = False
    delivery: bool = False


class WorkflowPatch(DTO):
    expected_version: StrictInt
    unpaid_timeout_minutes: StrictInt | None = Field(default=None,ge=5,le=10080)
    location_max_age_seconds: StrictInt | None = Field(default=None,ge=10,le=3600)
    location_max_accuracy_meters: StrictInt | None = Field(default=None,ge=1,le=1000)
    short_code_length: StrictInt | None = Field(default=None,ge=5,le=12)
    pauses: PauseConfig | None = None


class RetentionPatch(DTO):
    expected_version: StrictInt
    source_days: Literal[7] = 7
    final_days: Literal[30] = 30
    proof_days: Literal[30] = 30
    draft_hours: Literal[24] = 24
    export_hours: Literal[24] = 24
    trash_hours: Literal[24] = 24


PATCH_TYPES = {'prices':PricePatch,'services':ServicesPatch,'photo':PhotoPatch,'contact':ContactPatch,
               'announcement':AnnouncementPatch,'workflow':WorkflowPatch,'retention':RetentionPatch}


class ConfigFeatures(DTO):
    guest_checkout_enabled:bool
    customer_email_auth_available:bool
    promotions_enabled:bool
    resumable_uploads:bool
    server_drafts:bool
    after_sales:bool
    self_deletion_request:bool


class PricesView(DTO):
    bw_single:int|None
    bw_duplex:int|None
    color_single:int|None
    color_duplex:int|None
    photo:int|None
    corner:int|None
    side:int|None


class PublicConfigView(DTO):
    location_max_age_seconds:int
    location_max_accuracy_meters:float
    service_enabled:bool
    max_files:int
    max_mb:int
    max_order_mb:int
    max_pages:int
    max_copies:int
    max_pixels:int
    max_expanded_pages:int
    max_final_mb:int
    binding_approved:bool
    binding_limits:dict[str,int|None]
    formats:list[str]
    photo_enabled:bool
    photo_calibrated:bool
    photo_width_mm:float
    photo_height_mm:float
    margins:Margins
    wechat:str
    phone:str
    qr_url:str
    display_name:str
    guide:str
    announcement:str
    map_url:str
    map_version:int
    asset_id:str|None
    prices:PricesView
    versions:dict[str,int]
    version:int
    quote_minutes:int
    unpaid_timeout_minutes:int|None
    pauses:PauseConfig
    features:ConfigFeatures
    decisions:dict[str,str]


SECTION_VIEWS={}
for _name,_schema in PATCH_TYPES.items():
    fields={name:(field.annotation,field) for name,field in _schema.model_fields.items() if name!='expected_version'}
    fields['version']=(int,...)
    if _name=='services':fields['binding_approved']=(bool,True)
    # Quote validity is workflow-owned but not operator-patchable; the stored
    # section still exposes it so the admin read response validates.
    if _name=='workflow':fields['quote_minutes']=(int,None)
    SECTION_VIEWS[_name]=create_model(_name.title()+'ConfigView',__base__=DTO,**fields)
SectionsView=create_model('ConfigSectionsView',__base__=DTO,**{name:(value,None) for name,value in SECTION_VIEWS.items()})


class AdminConfigView(PublicConfigView):
    sections:SectionsView|None=None


@router.get('/public/config',response_model=Envelope[PublicConfigView])
def config_public(db=Depends(get_db)):
    return ok(public_config(db))


@router.get('/admin/config',response_model=Envelope[AdminConfigView])
def config_admin(db=Depends(get_db), actor:Actor=Depends(actor_dep)):
    require_admin(actor)
    result=public_config(db)
    if '*' in actor.capabilities or 'config.manage' in actor.capabilities:
        result['sections']={name:{'version':get_config(db,name).version,**get_config(db,name).data} for name in PATCH_TYPES}
    return ok(result)


def config_patch(section:str, payload:dict, db=Depends(get_db), actor:Actor=Depends(actor_dep)):
    require_admin(actor,'config.manage')
    if section not in PATCH_TYPES:
        raise DomainError('UNKNOWN_CONFIG_SECTION','该配置由专属接口管理',404)
    body=PATCH_TYPES[section].model_validate(payload)
    row=get_config(db,section)
    lock(db,ConfigSection,row.id,body.expected_version)
    update=body.model_dump(by_alias=True,exclude_unset=True,exclude={'expected_version'})
    if section != 'prices':
        update={key:value for key,value in update.items() if value is not None}
    data={**row.data,**update}
    if section=='photo':
        m=data['margins']
        if m['left']+m['right'] >= data['photoWidthMm'] or m['top']+m['bottom'] >= data['photoHeightMm']:
            raise DomainError('PHOTO_GEOMETRY_INVALID','边距必须小于实际纸张尺寸',422)
    if section=='contact' and data.get('qrUrl'):
        from .models import Asset
        from urllib.parse import urlparse
        path=urlparse(data['qrUrl']).path
        asset_id=path.split('/')[-2] if path.endswith('/content') else ''
        asset=db.get(Asset,asset_id)
        if not asset or asset.purpose!='PUBLIC_WECHAT_QR' or asset.status!='READY' or not data['qrUrl'].startswith('/api/v1/assets/'):
            raise DomainError('INVALID_PUBLIC_ASSET','请选择已上传并就绪的公开二维码资产',422)
    row.data=data
    touch(row)
    audit(db,actor,'CONFIG_UPDATED','CONFIG',section,data={'version':row.version,'fields':list(update)})
    return ok({'version':row.version,**row.data})


def _register_section(section,schema):
    def endpoint(payload,db=Depends(get_db),actor:Actor=Depends(actor_dep)):
        return config_patch(section,payload.model_dump(by_alias=True,exclude_unset=True),db,actor)
    endpoint.__name__='update_'+section.replace('-','_')+'_config'
    endpoint.__annotations__['payload']=schema
    router.add_api_route('/admin/config/'+section,endpoint,methods=['PATCH'],response_model=Envelope[SECTION_VIEWS[section]])


for _section,_type in PATCH_TYPES.items():
    _register_section(_section,_type)
