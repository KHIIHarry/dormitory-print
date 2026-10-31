from datetime import datetime, timedelta
import secrets
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import Field, StrictInt, field_validator
from sqlalchemy import and_, func, or_, select

from .config import get_config, public_config
from .core import (Actor, DTO, DomainError, Envelope, actor_dep, audit, authorize_order,
                   grant_scopes, lock, ok, owns_principal, require_admin, require_customer, touch)
from .db import get_db
from .models import (AccessGrant, Account, AnonymousLedger, Artifact, Asset, BindingTask, BusinessException, DeliveryAssignment,
    DeliveryProof, DeliverySlot, Location, Order, OrderFile, OrderRevision, PackingTask, Payment, Preparation,
    Principal, PrintAttempt, PrintTask, Quote, Receipt, ReceiptAdjustment, utcnow)
from .growth_schemas import QuoteDTO
from .supplementary_schemas import SupplementarySummaryDTO

router=APIRouter()
STATUS=Literal['PENDING_CONFIRMATION','PENDING_PRINT','READY_FOR_DELIVERY','DELIVERING','DELIVERED','CANCELLED','DELIVERY_EXCEPTION']


class Contact(DTO):
    wechat_nickname:str=Field(min_length=1,max_length=120)
    phone:str=Field(min_length=7,max_length=24,pattern=r'^\+?[0-9 -]+$')

    @field_validator('wechat_nickname')
    @classmethod
    def clean_nickname(cls,value):
        value=value.strip()
        if not value:
            raise ValueError('微信昵称不能为空')
        return value


class PreviewAck(DTO):
    artifact_id:str
    sha256:str=Field(pattern='^[a-f0-9]{64}$')


class OrderSubmit(DTO):
    preparation_id:str
    quote_id:str
    preview_acks:list[PreviewAck]
    contact:Contact
    location_id:str
    slot_id:str
    customer_note:str=Field(default='',max_length=30)

    @field_validator('customer_note',mode='before')
    @classmethod
    def note_length(cls,value):
        if not isinstance(value,str):
            raise ValueError('订单备注必须为文字')
        value=value.strip()
        if len(value)>30:
            raise ValueError('订单备注最多30字')
        return value


class RevisionSubmit(OrderSubmit):
    expected_version:StrictInt


class VersionReason(DTO):
    expected_version:StrictInt
    reason:str=Field(default='',max_length=1000)


class LocationInput(DTO):
    expected_version:StrictInt
    location_id:str


class SlotProposal(DTO):
    expected_version:StrictInt
    slot_id:str
    reason:str=Field(min_length=1,max_length=1000)


class SlotAgreement(DTO):
    expected_version:StrictInt
    slot_id:str
    confirm:Literal[True]


class ConfirmPayment(DTO):
    expected_version:StrictInt
    content_revision:StrictInt
    quote_id:str
    received_cents:StrictInt=Field(ge=0)
    settlement_kind:Literal['CASH_MANUAL','ZERO_PAYABLE']
    note:str=Field(default='',max_length=1000)
    receipt_id:str|None=None


class ReceiptInput(DTO):
    amount_cents:StrictInt=Field(gt=0)
    reason:str=Field(min_length=1,max_length=1000)


class ReceiptRefundInput(VersionReason):
    amount_cents:StrictInt=Field(gt=0)


class ReceiptView(DTO):
    id:str
    order_id:str
    amount_cents:int
    refunded_cents:int
    refundable_cents:int
    status:str
    version:int
    created_at:str


class ReceiptList(DTO):
    items:list[ReceiptView]
    next_cursor:str|None=None


class AdminOrderPatch(DTO):
    expected_version:StrictInt
    note:str|None=Field(default=None,max_length=3000)
    priority:bool|None=None


class HistoryView(DTO):
    title:str
    time:str
    description:str


class Blocker(DTO):
    code:str
    message:str
    related_id:str|None=None


class ArtifactView(DTO):
    id:str
    file_id:str
    segment_id:str
    sha256:str
    url:str
    pages:int
    sheets:int
    blank_backs:int
    range:str
    copies:int
    color:str
    sides:str
    flip:str
    content_revision:int=1
    pdf_pages:int
    expanded_copies:int
    source_page_map_available:bool=True


class SegmentView(DTO):
    id:str
    client_segment_id:str|None=None
    version:int
    range:str
    color_mode:str
    sides:str
    flip:str
    copies:int


class PhotoView(DTO):
    fit:str='CONTAIN'
    rotation_degrees:int=0
    orientation:str='LANDSCAPE'
    copies:int=1
    zoom:float=1
    x:float=0
    y:float=0


class FileView(DTO):
    id:str
    client_file_id:str
    version:int
    asset_id:str
    name:str
    size:int
    pages:int
    kind:str
    status:Literal['READY']='READY'
    segments:list[SegmentView]
    excluded_pages:list[int]
    binding_type:str
    binding_status:str
    binding_task_id:str|None=None
    binding_version:int|None=None
    print_status:str
    photo:PhotoView
    final_artifacts:list[ArtifactView]
    task_results:dict[str,str]
    task_versions:dict[str,int]
    attempt_ids:dict[str,str]
    attempt_versions:dict[str,int]
    artifact_id:str|None=None
    sha256:str|None=None
    pdf_url:str|None=None


class SettlementView(DTO):
    settlement_kind:str
    cash_received_cents:int
    discount_cents:int
    confirmed_at:str
    confirmed_by:str


class PositionView(DTO):
    latitude:float
    longitude:float
    accuracy_meters:float
    captured_at:str
    coordinate_system:Literal['WGS84']='WGS84'


class ProofView(DTO):
    photo_url:str|None=None
    skip_photo_reason:str|None=None
    position:PositionView|None=None
    skip_location_reason:str|None=None
    location_failure_code:str|None=None
    placement_note:str=''
    delivered_at:str


class OrderView(DTO):
    id:str
    pickup_code:str
    version:int
    content_revision:int
    status:STATUS
    nickname:str
    phone:str
    location_id:str
    slot_id:str
    requested_slot_id:str
    confirmed_slot_id:str|None=None
    slot_status:Literal['REQUESTED','CONFIRMED']
    slot_proposal:dict|None=None
    created_at:str
    close_at:str
    total_cents:int
    refunded_cents:int
    current_quote_id:str
    quote:QuoteDTO
    checkout_account_id:str|None=None
    checkout_auth_kind:str
    owner_account_id:str|None=None
    owner_principal_id:str|None=None
    access_kind:str
    files:list[FileView]
    packing_status:str
    packing_version:int|None=None
    packing_checklist_version:int
    address_review:bool
    actual_started_at:str|None=None
    refund_locked:bool
    note:str=''
    customer_note:str=''
    priority:bool
    batch_id:str|None=None
    delivery_assignment_id:str|None=None
    settlement:SettlementView|None=None
    proof:ProofView|None=None
    history:list[HistoryView]
    allowed_actions:list[str]
    blockers:list[Blocker]
    supplementary:SupplementarySummaryDTO|None=None


class OrderList(DTO):
    items:list[OrderView]
    next_cursor:str|None=None
    total:int|None=None


class OrderResult(DTO):
    order:OrderView
    access_token:str|None=None


def quote_view(quote):
    data=dict(quote.data or {})
    data.update({'id':quote.id,'priceVersion':quote.price_version,'expiresAt':quote.expires_at.isoformat(),
        'subtotalCents':quote.subtotal_cents,'discountCents':quote.discount_cents,'totalCents':quote.total_cents,
        'checkoutAccountId':quote.checkout_account_id})
    data.setdefault('promotionRuleVersion','NONE')
    return data


def add_history(order,title,description):
    data=dict(order.data or {})
    history=list(data.get('history',[]))
    history.insert(0,{'title':title,'time':utcnow().isoformat(),'description':description})
    data['history']=history
    order.data=data


def _artifact_view(artifact,file_id,segment_id):
    values=artifact.settings or {}
    return {'id':artifact.id,'fileId':file_id,'segmentId':segment_id,'sha256':artifact.sha256,
        'url':f'/api/v1/assets/{artifact.asset_id}/content','pages':artifact.pages,'sheets':artifact.sheets,
        'blankBacks':values.get('blankBacks',0),'range':str(values.get('range','照片')),
        'copies':values.get('copies',1),'color':values.get('colorMode',values.get('color','COLOR')),
        'sides':values.get('sides','SINGLE'),'flip':values.get('flip','NONE'),
        'contentRevision':values.get('contentRevision',1),'pdfPages':artifact.pages,
        'expandedCopies':values.get('copies',1),'sourcePageMapAvailable':bool(artifact.page_map)}


def _scope_actions(db,order,actor):
    owner=owns_principal(db,actor,order.principal_id)
    scopes=grant_scopes(db,actor,order.id)
    if owner or actor.staff_role=='ADMIN':
        scopes.update(('ORDER_VIEW','FINAL_PDF_PREVIEW','DELIVERY_PROOF_VIEW','ORDER_REVISE_PENDING',
            'ORDER_CANCEL_PENDING','ORDER_CHANGE_LOCATION_PRE_DELIVERY','ORDER_AFTER_SALES','ORDER_AGREE_SLOT'))
    allowed={'ORDER_VIEW'}
    allowed.update(scopes & {'FINAL_PDF_PREVIEW','DELIVERY_PROOF_VIEW','ORDER_AFTER_SALES','ORDER_AGREE_SLOT'})
    if order.status=='PENDING_CONFIRMATION':
        allowed.update(scopes & {'ORDER_REVISE_PENDING','ORDER_CANCEL_PENDING'})
    if order.status in ('PENDING_CONFIRMATION','PENDING_PRINT','READY_FOR_DELIVERY','DELIVERY_EXCEPTION'):
        allowed.update(scopes & {'ORDER_CHANGE_LOCATION_PRE_DELIVERY'})
    if not order.settlement_kind:
        allowed.discard('ORDER_AFTER_SALES')
    if not (order.data or {}).get('slotProposal') or order.status!='PENDING_CONFIRMATION':
        allowed.discard('ORDER_AGREE_SLOT')
    return sorted(allowed)


def order_view(db,order,actor):
    authorize_order(db,actor,order)
    actions=_scope_actions(db,order,actor)
    owner=owns_principal(db,actor,order.principal_id)
    access_kind='OWNER' if owner and actor.account_id else 'GUEST_OWNER' if owner else 'IMPORTED_GRANT' if actor.account_id else 'ORDER_GRANT'
    files=[]
    for file in db.scalars(select(OrderFile).where(OrderFile.order_id==order.id,OrderFile.revision==order.content_revision).order_by(OrderFile.created_at,OrderFile.id)):
        source=db.get(Asset,file.asset_id)
        tasks=list(db.scalars(select(PrintTask).where(PrintTask.file_id==file.id,PrintTask.active.is_(True)).order_by(PrintTask.created_at,PrintTask.id)))
        segments=[];artifacts=[];results={};attempt_ids={};attempt_versions={};task_versions={}
        for task in tasks:
            artifact=db.get(Artifact,task.artifact_id)
            settings={**(artifact.settings or {}),**(task.settings or {})}
            key='photo' if file.kind=='PHOTO' else task.id
            if file.kind=='DOCUMENT':
                segments.append({'id':task.id,'clientSegmentId':artifact.client_segment_id,'version':task.version,
                    'range':str(settings.get('range','1')),'colorMode':settings.get('colorMode','BW'),
                    'sides':settings.get('sides','SINGLE'),'flip':settings.get('flip','NONE'),'copies':settings.get('copies',1)})
            results[key]=task.status;task_versions[key]=task.version
            attempt=db.scalar(select(PrintAttempt).where(PrintAttempt.task_id==task.id).order_by(PrintAttempt.created_at.desc(),PrintAttempt.id.desc()).limit(1))
            if attempt and not attempt.outcome:
                attempt_ids[key]=attempt.id;attempt_versions[key]=attempt.version
            if 'FINAL_PDF_PREVIEW' in actions or actor.staff_role=='ADMIN':
                artifacts.append(_artifact_view(artifact,file.id,key))
        binding=db.scalar(select(BindingTask).where(BindingTask.file_id==file.id))
        statuses=list(results.values())
        aggregate='COMPLETED' if statuses and all(v=='COMPLETED' for v in statuses) else 'ATTEMPTED' if 'ATTEMPTED' in statuses else 'EXCEPTION' if 'EXCEPTION' in statuses else 'PENDING'
        snap=file.data or {}
        photo=snap.get('photo') or {key:snap.get(key,value) for key,value in {'fit':'CONTAIN','rotationDegrees':0,'orientation':'LANDSCAPE','copies':1}.items()}
        photo={key:photo[key] for key in ('fit','rotationDegrees','orientation','copies','zoom','x','y') if key in photo}
        result={'id':file.id,'clientFileId':file.client_file_id,'version':file.version,'assetId':file.asset_id,
            'name':source.name if source else snap.get('name','内容已清理'),'size':source.size if source else 0,'pages':(source.pages or 1) if source else 0,
            'kind':file.kind,'status':'READY','segments':segments,'excludedPages':snap.get('excludedPages',[]),
            'bindingType':file.binding_type,'bindingStatus':binding.status if binding else 'NOT_REQUIRED',
            'bindingTaskId':binding.id if binding else None,'bindingVersion':binding.version if binding else None,
            'printStatus':aggregate,'photo':photo,'finalArtifacts':artifacts,'taskResults':results,'taskVersions':task_versions,
            'attemptIds':attempt_ids,'attemptVersions':attempt_versions,'artifactId':artifacts[0]['id'] if artifacts else None,
            'sha256':artifacts[0]['sha256'] if artifacts else None,'pdfUrl':artifacts[0]['url'] if artifacts else None}
        files.append(result)
    quote=db.get(Quote,order.quote_id)
    assignment=db.scalar(select(DeliveryAssignment).where(DeliveryAssignment.order_id==order.id,DeliveryAssignment.active.is_(True)))
    proof=db.scalar(select(DeliveryProof).where(DeliveryProof.order_id==order.id,DeliveryProof.active.is_(True)).order_by(DeliveryProof.created_at.desc()).limit(1))
    proof_view=None
    if proof and ('DELIVERY_PROOF_VIEW' in actions or actor.staff_role=='ADMIN'):
        p=proof.data or {}
        proof_view={'photoUrl':f'/api/v1/assets/{proof.photo_asset_id}/content' if proof.photo_asset_id else None,
            'skipPhotoReason':p.get('skipPhotoReason'),'position':p.get('position'),'skipLocationReason':p.get('skipLocationReason'),
            'locationFailureCode':p.get('locationFailureCode'),'placementNote':p.get('placementNote',''),
            'deliveredAt':p.get('serverDeliveredAt',p.get('deliveredAt',proof.created_at.isoformat()))}
    blockers=[{'code':e.type,'message':e.reason,'relatedId':e.id} for e in db.scalars(select(BusinessException).where(BusinessException.order_id==order.id,BusinessException.status.in_(['OPEN','IN_PROGRESS'])))]
    if order.refund_locked:
        blockers.append({'code':'REFUND_REVIEW_PENDING','message':'退款审核中，已暂停新生产'})
    packing=db.scalar(select(PackingTask).where(PackingTask.order_id==order.id))
    data=order.data or {}
    principal=db.get(Principal,order.principal_id)
    from .supplementary import supplementary_summary
    extra_summary=supplementary_summary(db,order)
    return {'id':order.id,'pickupCode':order.pickup_code,'version':order.version,'contentRevision':order.content_revision,
        'status':order.status,'nickname':order.nickname,'phone':order.phone,'locationId':order.location_id,
        'slotId':order.confirmed_slot_id or order.requested_slot_id,'requestedSlotId':order.requested_slot_id,
        'confirmedSlotId':order.confirmed_slot_id,'slotStatus':'CONFIRMED' if order.confirmed_slot_id else 'REQUESTED',
        'slotProposal':data.get('slotProposal'),'createdAt':order.created_at.isoformat(),'closeAt':order.close_at.isoformat(),
        'totalCents':order.total_cents,'refundedCents':order.refunded_cents,'currentQuoteId':order.quote_id,'quote':quote_view(quote),
        'checkoutAccountId':order.checkout_account_id,'checkoutAuthKind':data.get('checkoutAuthKind','REGISTERED' if order.checkout_account_id else 'ANONYMOUS'),
        'ownerAccountId':principal.account_id if owner and principal else None,'ownerPrincipalId':order.principal_id if owner else None,
        'accessKind':access_kind,'files':files,'packingStatus':order.packing_status,'packingVersion':packing.version if packing else None,
        'packingChecklistVersion':order.content_revision,'addressReview':order.address_review,
        'actualStartedAt':order.actual_started_at.isoformat() if order.actual_started_at else None,'refundLocked':order.refund_locked,
        'note':order.note if actor.staff_role=='ADMIN' else '', 'customerNote':order.customer_note,
        'priority':bool(data.get('priority',False)),'batchId':assignment.batch_id if assignment else None,
        'deliveryAssignmentId':assignment.id if assignment else None,'settlement':data.get('settlement'),'proof':proof_view,
        'history':data.get('history',[]),'allowedActions':actions,'blockers':blockers,'supplementary':extra_summary}


def _current_ready(db,actor,payload,order=None):
    prep=lock(db,Preparation,payload.preparation_id)
    if not owns_principal(db,actor,prep.principal_id):
        if not order or prep.replaces_order_id!=order.id or not prep.source_grant_id:
            raise DomainError('NOT_FOUND','准备单不存在或不可访问',404)
        authorize_order(db,actor,order,'ORDER_REVISE_PENDING')
    if prep.replaces_order_id and not order:
        raise DomainError('INVALID_PREPARATION','修订单不能用于创建另一订单',422)
    if prep.cancel_requested or prep.status!='READY' or prep.expires_at<=utcnow():
        raise DomainError('FILE_NOT_READY','最终文件尚未就绪或准备单已失效',409)
    if order and (prep.replaces_order_id!=order.id or prep.expected_order_version!=order.version):
        raise DomainError('VERSION_CONFLICT','准备单对应的订单版本已变化',409)
    if prep.source_grant_id:
        source_scopes=grant_scopes(db,Actor(grant_ids=[prep.source_grant_id]),order.id if order else '')
        if not order or 'ORDER_REVISE_PENDING' not in source_scopes:
            raise DomainError('SOURCE_GRANT_REVOKED','原委托授权已撤销或过期，请重新取得授权',403)
    artifacts=list(db.scalars(select(Artifact).where(Artifact.preparation_id==prep.id)))
    expected={(a.id,a.sha256) for a in artifacts}
    actual=[(ack.artifact_id,ack.sha256) for ack in payload.preview_acks]
    if not expected or len(actual)!=len(expected) or len(set(actual))!=len(actual) or set(actual)!=expected:
        raise DomainError('PREVIEW_REQUIRED','请逐项确认当前全部最终PDF',422)
    if any(not (asset:=db.get(Asset,a.asset_id)) or asset.status!='READY' or asset.deleted_at for a in artifacts):
        raise DomainError('FILE_NOT_READY','最终文件不可用，请重新准备',409)
    quote=lock(db,Quote,payload.quote_id)
    if quote.preparation_id!=prep.id or prep.quote_id!=quote.id or quote.expires_at<=utcnow():
        raise DomainError('QUOTE_EXPIRED','报价已过期，请重新报价确认',409)
    if quote.price_version!=get_config(db,'prices').version:
        raise DomainError('QUOTE_EXPIRED','价格已变化，请重新确认报价',409)
    from .benefits import validate_quote_entitlement
    validate_quote_entitlement(db,prep,quote)
    if quote.checkout_account_id!=prep.checkout_account_id:
        raise DomainError('CHECKOUT_AUTH_CHANGED','结算身份已变化，请重新确认',409)
    if not order and quote.checkout_account_id and quote.checkout_account_id!=actor.account_id:
        raise DomainError('CHECKOUT_AUTH_CHANGED','请重新登录本次结算账号后提交',409)
    if order and quote.checkout_account_id!=order.checkout_account_id:
        raise DomainError('CHECKOUT_AUTH_CHANGED','修订既有订单须保持原结算身份',409)
    if order and quote.checkout_account_id and quote.checkout_account_id!=actor.account_id:
        original_quote=db.get(Quote,order.quote_id)
        if quote.selected_coupon_id!=original_quote.selected_coupon_id:
            raise DomainError('OWNER_REQUOTE_REQUIRED','原优惠需由结算账号本人重新确认；委托编辑不能换用其他账号券或静默取消原券',409)
    config=public_config(db)
    workflow=get_config(db,'workflow').data
    if not config.get('serviceEnabled') or workflow.get('pauses',{}).get('newOrders'):
        raise DomainError('SERVICE_DISABLED','当前暂停接单',409)
    if not workflow.get('unpaidTimeoutMinutes'):
        raise DomainError('WORKFLOW_NOT_CONFIGURED','管理员尚未配置待确认订单期限',409)
    location=db.get(Location,payload.location_id)
    slot=db.get(DeliverySlot,payload.slot_id)
    if not location or not location.enabled:
        raise DomainError('LOCATION_UNAVAILABLE','所选楼栋当前不可用',409)
    if not slot or not slot.enabled or slot.ends_at<=utcnow():
        raise DomainError('SLOT_UNAVAILABLE','所选申请时段已关闭，请重新选择',409)
    cutoff=(slot.data or {}).get('cutoffAt')
    if cutoff and datetime.fromisoformat(cutoff.replace('Z','+00:00'))<=utcnow():
        raise DomainError('SLOT_UNAVAILABLE','该时段已过申请截止时间',409)
    return prep,quote,artifacts,location,slot


def _activate(db,order,prep,quote,artifacts):
    for old in db.scalars(select(PrintTask).where(PrintTask.order_id==order.id,PrintTask.active.is_(True))):
        old.active=False;old.status='VOIDED';touch(old)
    db.add(OrderRevision(order_id=order.id,number=order.content_revision,preparation_id=prep.id,quote_id=quote.id,
        snapshot={'input':prep.input,'artifacts':[{'id':a.id,'sha256':a.sha256} for a in artifacts],
                  'quote':quote_view(quote),'customerNote':order.customer_note}))
    for file_input in prep.input.get('files',[]):
        file=OrderFile(order_id=order.id,revision=order.content_revision,client_file_id=file_input['clientFileId'],
            asset_id=file_input['assetId'],kind=file_input['kind'],binding_type=file_input.get('bindingType','NONE'),data=dict(file_input))
        db.add(file);db.flush()
        own=[a for a in artifacts if a.client_file_id==file.client_file_id]
        if not own:
            raise DomainError('FILE_NOT_READY','最终任务与文件清单不一致',409)
        for artifact in own:
            db.add(PrintTask(order_id=order.id,file_id=file.id,artifact_id=artifact.id,settings=dict(artifact.settings)))
        if file.binding_type!='NONE':
            db.add(BindingTask(order_id=order.id,file_id=file.id,status='WAITING_PRINT',data={'method':file.binding_type}))
    packing=db.scalar(select(PackingTask).where(PackingTask.order_id==order.id))
    if packing:
        packing.status='BLOCKED';packing.manifest_revision=order.content_revision;packing.data={};touch(packing)
    else:
        db.add(PackingTask(order_id=order.id,manifest_revision=order.content_revision))
    db.flush()


def require_order_account(db, actor):
    """Serialize an internal order submission with account deletion.

    The HTTP dependency already revalidates the account session, but
    ``submit_order`` is also called directly by internal workflows and tests.
    Lock the Account row and re-read its current lifecycle state so a deletion
    that commits first cannot be bypassed by an already-built Actor.
    """
    if not actor.account_id:
        return None
    from .auth import _account_active
    # ``populate_existing`` is required: this Session may already hold an
    # ACTIVE Account from before a concurrent deletion committed.  Without it
    # SQLAlchemy returns the stale identity-map row and the guard could accept
    # an order for a DELETION_PENDING account.  This call only reads state
    # before any submission mutations, so refreshing is safe.
    account = db.scalar(
        select(Account).where(Account.id == actor.account_id).with_for_update()
        .execution_options(populate_existing=True))
    if account is None or not _account_active(account):
        raise DomainError('AUTH_REQUIRED', '账号状态已变更，请重新登录', 401)
    return account


def submit_order(db,actor,payload):
    require_customer(actor)
    require_order_account(db, actor)
    prep,quote,artifacts,location,slot=_current_ready(db,actor,payload)
    if db.scalar(select(Order.id).where(Order.preparation_id==prep.id)):
        raise DomainError('PREPARATION_ALREADY_SUBMITTED','该准备单已经提交，请核实已有订单',409)
    workflow=get_config(db,'workflow').data
    alphabet='ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    code=''.join(secrets.choice(alphabet) for _ in range(workflow['shortCodeLength']))
    while db.scalar(select(Order.id).where(Order.pickup_code==code)):
        code=''.join(secrets.choice(alphabet) for _ in range(workflow['shortCodeLength']))
    order=Order(principal_id=prep.principal_id,checkout_account_id=quote.checkout_account_id,pickup_code=code,
        preparation_id=prep.id,quote_id=quote.id,total_cents=quote.total_cents,nickname=payload.contact.wechat_nickname.strip(),
        phone=payload.contact.phone,customer_note=payload.customer_note,location_id=location.id,requested_slot_id=slot.id,
        close_at=utcnow()+timedelta(minutes=workflow['unpaidTimeoutMinutes']),data={'quote':quote_view(quote),
        'checkoutAuthKind':'REGISTERED' if quote.checkout_account_id else 'ANONYMOUS',
        'locationSnapshot':{'id':location.id,'name':location.name,'mapVersion':location.map_version},
        'requestedSlotSnapshot':{'id':slot.id,'date':slot.date,'name':slot.name},'history':[]})
    db.add(order);db.flush()
    from .growth import reserve_coupon
    reserve_coupon(db,order,quote)
    _activate(db,order,prep,quote,artifacts)
    add_history(order,'订单已提交','等待管理员人工结算，所选为申请时段，确认前不保证名额')
    audit(db,actor,'ORDER_CREATED','ORDER',order.id)
    from .growth import notify_order_event
    notify_order_event(db,order,'CREATED')
    return order


def _list_query(db,actor,admin=False):
    query=select(Order).where(Order.deleted_at.is_(None))
    if admin:
        return query
    owner_ids=select(Principal.id).where(or_(Principal.id==actor.principal_id,
        Principal.id==actor.guest_principal_id,Principal.account_id==actor.account_id if actor.account_id else False))
    grants=select(AccessGrant.order_id).where(or_(AccessGrant.account_id==actor.account_id if actor.account_id else False,
        AccessGrant.id.in_(actor.grant_ids)),AccessGrant.revoked_at.is_(None),AccessGrant.expires_at>utcnow())
    return query.where(or_(Order.principal_id.in_(owner_ids),Order.id.in_(grants)))


def _order_list(db,actor,admin=False,cursor=None,limit=50,**filters):
    query=_list_query(db,actor,admin)
    for name,column in (('status',Order.status),('building',Order.location_id),('slot',Order.requested_slot_id)):
        if filters.get(name):query=query.where(column==filters[name])
    if q:=filters.get('q'):
        query=query.where(or_(Order.pickup_code.ilike('%'+q+'%'),Order.phone.ilike('%'+q+'%'),Order.nickname.ilike('%'+q+'%')))
    if filters.get('batch'):
        query=query.where(Order.id.in_(select(DeliveryAssignment.order_id).where(DeliveryAssignment.batch_id==filters['batch'],DeliveryAssignment.active.is_(True))))
    if cursor:
        anchor=db.get(Order,cursor)
        if anchor:
            query=query.where(or_(Order.created_at<anchor.created_at,and_(Order.created_at==anchor.created_at,Order.id<anchor.id)))
    rows=list(db.scalars(query.order_by(Order.created_at.desc(),Order.id.desc()).limit(limit+1)))
    items=[]
    for row in rows[:limit]:
        try:items.append(order_view(db,row,actor))
        except DomainError as error:
            if error.status!=404:raise
    return {'items':items,'nextCursor':rows[limit-1].id if len(rows)>limit else None}


def require_order_admin(actor):
    require_admin(actor)
    allowed={'orders.view','orders.manage','production.manage','delivery.manage','payment.manage','customer.manage'}
    if '*' not in actor.capabilities and not actor.capabilities.intersection(allowed):
        raise DomainError('FORBIDDEN','无权查看管理订单',403)


@router.post('/orders',response_model=Envelope[OrderResult])
def create_order(body:OrderSubmit,db=Depends(get_db),actor:Actor=Depends(actor_dep)):
    order=submit_order(db,actor,body)
    from .auth import issue_order_grant
    _,token=issue_order_grant(db,order,actor)
    return ok({'order':order_view(db,order,actor),'accessToken':token})


@router.get('/customer/orders',response_model=Envelope[OrderList])
def customer_orders(cursor:str|None=None,limit:int=Query(default=50,ge=1,le=100),db=Depends(get_db),actor:Actor=Depends(actor_dep)):
    return ok(_order_list(db,actor,cursor=cursor,limit=limit))


@router.get('/orders/{order_id}',response_model=Envelope[OrderView])
def customer_order(order_id:str,db=Depends(get_db),actor:Actor=Depends(actor_dep)):
    return ok(order_view(db,db.get(Order,order_id),actor))


@router.get('/admin/orders',response_model=Envelope[OrderList])
def admin_orders(q:str|None=None,status:str|None=None,building:str|None=None,slot:str|None=None,batch:str|None=None,
    cursor:str|None=None,limit:int=Query(default=50,ge=1,le=100),db=Depends(get_db),actor:Actor=Depends(actor_dep)):
    require_order_admin(actor)
    return ok(_order_list(db,actor,True,cursor,limit,q=q,status=status,building=building,slot=slot,batch=batch))


@router.get('/admin/orders/{order_id}',response_model=Envelope[OrderView])
def admin_order(order_id:str,db=Depends(get_db),actor:Actor=Depends(actor_dep)):
    require_order_admin(actor)
    return ok(order_view(db,db.get(Order,order_id),actor))


@router.post('/orders/{order_id}/revisions',response_model=Envelope[OrderResult])
def revise_order(order_id:str,body:RevisionSubmit,db=Depends(get_db),actor:Actor=Depends(actor_dep)):
    order=lock(db,Order,order_id,body.expected_version)
    authorize_order(db,actor,order,'ORDER_REVISE_PENDING')
    if order.status!='PENDING_CONFIRMATION' or order.settlement_kind:
        raise DomainError('INVALID_TRANSITION','付款后不修改原单内容，请通过售后或新订单处理',409)
    prep,quote,artifacts,location,slot=_current_ready(db,actor,body,order)
    from .growth import release_coupon,reserve_coupon
    old_quote=db.get(Quote,order.quote_id)
    if old_quote.selected_coupon_id!=quote.selected_coupon_id:
        release_coupon(db,order)
    order.quote_id=quote.id
    reserve_coupon(db,order,quote)
    order.content_revision+=1;order.preparation_id=prep.id;order.quote_id=quote.id;order.total_cents=quote.total_cents
    order.nickname=body.contact.wechat_nickname.strip();order.phone=body.contact.phone;order.customer_note=body.customer_note
    order.location_id=location.id;order.requested_slot_id=slot.id
    order.data={**order.data,'quote':quote_view(quote)}
    _activate(db,order,prep,quote,artifacts)
    add_history(order,'订单内容已修订','新内容、最终PDF及金额已确认');touch(order)
    audit(db,actor,'ORDER_REVISED','ORDER',order.id)
    return ok({'order':order_view(db,order,actor)})


def cancel_pending(db,order,reason,actor=None):
    if order.status!='PENDING_CONFIRMATION' or order.settlement_kind:
        raise DomainError('INVALID_TRANSITION','该订单当前不能直接撤销',409)
    from .growth import release_coupon
    release_coupon(db,order)
    for assignment in db.scalars(select(DeliveryAssignment).where(DeliveryAssignment.order_id==order.id,DeliveryAssignment.active.is_(True)).with_for_update()):
        assignment.active=False;assignment.status='CANCELLED';touch(assignment)
    order.status='CANCELLED'
    order.data={**order.data,'closeReason':reason}
    add_history(order,'订单已关闭',reason);touch(order)
    if actor:audit(db,actor,'ORDER_CANCELLED','ORDER',order.id,reason)
    from .growth import notify_order_event
    notify_order_event(db,order,'STATUS_UPDATED')


@router.post('/orders/{order_id}/cancel',response_model=Envelope[OrderView])
def cancel_order(order_id:str,body:VersionReason,db=Depends(get_db),actor:Actor=Depends(actor_dep)):
    order=lock(db,Order,order_id,body.expected_version);authorize_order(db,actor,order,'ORDER_CANCEL_PENDING')
    cancel_pending(db,order,body.reason or '客户撤销',actor)
    return ok(order_view(db,order,actor))


@router.patch('/orders/{order_id}/location',response_model=Envelope[OrderView])
def change_location(order_id:str,body:LocationInput,db=Depends(get_db),actor:Actor=Depends(actor_dep)):
    order=lock(db,Order,order_id,body.expected_version);authorize_order(db,actor,order,'ORDER_CHANGE_LOCATION_PRE_DELIVERY')
    if order.status not in ('PENDING_CONFIRMATION','PENDING_PRINT','READY_FOR_DELIVERY','DELIVERY_EXCEPTION'):
        raise DomainError('INVALID_TRANSITION','实际配送开始后不能自行修改楼栋',409)
    location=db.get(Location,body.location_id)
    if not location or not location.enabled:raise DomainError('LOCATION_UNAVAILABLE','楼栋不可用',409)
    assignment=db.scalar(select(DeliveryAssignment).where(DeliveryAssignment.order_id==order.id,DeliveryAssignment.active.is_(True)).with_for_update())
    if assignment and assignment.status=='STARTED':raise DomainError('INVALID_TRANSITION','已开始配送',409)
    order.location_id=location.id
    order.data={**order.data,'locationSnapshot':{'id':location.id,'name':location.name,'mapVersion':location.map_version}}
    if assignment:assignment.address_review=True;order.address_review=True;touch(assignment)
    add_history(order,'配送楼栋已修改','已分配的配送安排须管理员复核');touch(order)
    return ok(order_view(db,order,actor))


@router.patch('/admin/orders/{order_id}',response_model=Envelope[OrderView])
def update_admin_order(order_id:str,body:AdminOrderPatch,db=Depends(get_db),actor:Actor=Depends(actor_dep)):
    require_admin(actor,'orders.manage');order=lock(db,Order,order_id,body.expected_version)
    if body.note is not None:order.note=body.note
    if body.priority is not None:order.data={**order.data,'priority':body.priority}
    touch(order);audit(db,actor,'ORDER_INTERNAL_UPDATED','ORDER',order.id)
    return ok(order_view(db,order,actor))


@router.post('/admin/orders/{order_id}/slot-proposals',response_model=Envelope[OrderView])
def propose_slot(order_id:str,body:SlotProposal,db=Depends(get_db),actor:Actor=Depends(actor_dep)):
    require_admin(actor,'payment.manage');order=lock(db,Order,order_id,body.expected_version)
    if order.status!='PENDING_CONFIRMATION':raise DomainError('INVALID_TRANSITION','仅待结算订单可确认申请时段',409)
    slot=lock(db,DeliverySlot,body.slot_id)
    if not slot.enabled or slot.occupied>=slot.capacity or slot.ends_at<=utcnow():raise DomainError('SLOT_UNAVAILABLE','新时段不可用',409)
    order.data={**order.data,'slotProposal':{'slotId':slot.id,'reason':body.reason,'confirmed':False}}
    touch(order);audit(db,actor,'SLOT_PROPOSED','ORDER',order.id,body.reason)
    return ok(order_view(db,order,actor))


@router.post('/orders/{order_id}/slot-agreement',response_model=Envelope[OrderView])
def agree_slot(order_id:str,body:SlotAgreement,db=Depends(get_db),actor:Actor=Depends(actor_dep)):
    order=lock(db,Order,order_id,body.expected_version);authorize_order(db,actor,order,'ORDER_AGREE_SLOT')
    proposal=(order.data or {}).get('slotProposal')
    if order.status!='PENDING_CONFIRMATION' or not proposal or proposal['slotId']!=body.slot_id:
        raise DomainError('INVALID_TRANSITION','待确认时段已变化',409)
    order.data={**order.data,'slotProposal':{**proposal,'confirmed':True,'confirmedAt':utcnow().isoformat()}}
    touch(order)
    return ok(order_view(db,order,actor))


def _receipt(db,order,actor,amount,note):
    receipt=Receipt(order_id=order.id,amount_cents=amount,staff_id=actor.staff_id,data={'reason':note})
    db.add(receipt);db.flush()
    audit(db,actor,'CASH_RECEIPT_RECORDED','RECEIPT',receipt.id)
    return receipt


def settle_order(db,actor,order,body):
    require_admin(actor,'payment.manage')
    if order.content_revision!=body.content_revision or order.quote_id!=body.quote_id:
        raise DomainError('VERSION_CONFLICT','内容或报价已变化，请重新核对',409)
    receipt=None
    if body.receipt_id:
        receipt=lock(db,Receipt,body.receipt_id)
        if receipt.order_id!=order.id or receipt.status!='UNMATCHED':raise DomainError('RECEIPT_UNAVAILABLE','该收款记录不可分配',409)
        refunded=db.scalar(select(func.coalesce(func.sum(ReceiptAdjustment.amount_cents),0)).where(ReceiptAdjustment.receipt_id==receipt.id))
        if receipt.amount_cents-refunded!=body.received_cents:raise DomainError('RECEIPT_AMOUNT_MISMATCH','待核对收款净额与本次分配金额不一致',409)
    elif body.received_cents>0:
        receipt=_receipt(db,order,actor,body.received_cents,body.note)
    if order.status!='PENDING_CONFIRMATION' or order.close_at<=utcnow() or order.settlement_kind:
        if receipt and not order.settlement_kind:
            receipt.data={**receipt.data,'refundRequired':True,'reasonCode':'CLOSED_ORDER'}
            raise DomainError('ORDER_CLOSED_CASH_REVIEW','订单已关闭或到期，已登记待核对实收，请线下退回后重新下单',409,{'receiptId':receipt.id,'refundRequired':True},commit=True)
        raise DomainError('INVALID_TRANSITION','订单当前不能重复结算',409)
    if body.received_cents!=order.total_cents or (order.total_cents==0)!=(body.settlement_kind=='ZERO_PAYABLE'):
        if receipt:
            db.add(BusinessException(order_id=order.id,type='PAYMENT_MISMATCH',reason='实收与订单应付不一致，待人工核对',blocks_production=True))
        raise DomainError('PAYMENT_AMOUNT_MISMATCH','实收金额必须与当前订单应付一致，已登记待核对实收',409,{'receiptId':receipt.id if receipt else None},commit=bool(receipt))
    proposal=(order.data or {}).get('slotProposal')
    slot_id=proposal['slotId'] if proposal and proposal.get('confirmed') else order.requested_slot_id
    slot=lock(db,DeliverySlot,slot_id)
    if not slot.enabled or slot.ends_at<=utcnow() or slot.occupied>=slot.capacity:
        raise DomainError('SLOT_CAPACITY_REVIEW_REQUIRED','申请时段已满或关闭，请先协调新时段并由客户确认',409,
            {'receiptId':receipt.id if receipt else None,'slotId':slot.id},commit=bool(receipt))
    if order.refund_locked:raise DomainError('REFUND_REVIEW_PENDING','退款审核中，不能结算',409)
    from .growth import settle_coupon
    settle_coupon(db,order)
    slot.occupied+=1;touch(slot)
    if receipt:receipt.status='ALLOCATED';touch(receipt)
    order.confirmed_slot_id=slot.id;order.cash_received_cents=body.received_cents;order.settlement_kind=body.settlement_kind
    order.status='PENDING_PRINT'
    quote=db.get(Quote,order.quote_id)
    summary={'settlementKind':body.settlement_kind,'cashReceivedCents':body.received_cents,'discountCents':quote.discount_cents,
             'confirmedAt':utcnow().isoformat(),'confirmedBy':actor.staff_id}
    order.data={**order.data,'settlement':summary,'confirmedSlotSnapshot':{'id':slot.id,'date':slot.date,'name':slot.name}}
    db.add(Payment(order_id=order.id,receipt_id=receipt.id if receipt else None,amount_cents=body.received_cents,kind=body.settlement_kind,staff_id=actor.staff_id,data={'quoteId':quote.id,'discountCents':quote.discount_cents}))
    # Matching the cash does not bypass an unrelated blocker.
    for mismatch in db.scalars(select(BusinessException).where(BusinessException.order_id==order.id,BusinessException.type=='PAYMENT_MISMATCH',BusinessException.status=='OPEN')):
        mismatch.status='RESOLVED';touch(mismatch)
    add_history(order,'零元结算已人工确认' if body.settlement_kind=='ZERO_PAYABLE' else '线下收款已人工确认','开始安排生产，配送时段已确认')
    touch(order)
    from .production import refresh_production
    db.flush();refresh_production(db,order)
    audit(db,actor,'PAYMENT_CONFIRMED','ORDER',order.id)
    from .growth import notify_order_event
    notify_order_event(db,order,'STATUS_UPDATED')
    return order


@router.post('/admin/orders/{order_id}/confirm-payment',response_model=Envelope[OrderView])
def confirm_payment(order_id:str,body:ConfirmPayment,db=Depends(get_db),actor:Actor=Depends(actor_dep)):
    require_admin(actor,'payment.manage');order=lock(db,Order,order_id,body.expected_version)
    settle_order(db,actor,order,body)
    return ok(order_view(db,order,actor))


@router.post('/admin/orders/{order_id}/receipts')
def record_receipt(order_id:str,body:ReceiptInput,db=Depends(get_db),actor:Actor=Depends(actor_dep)):
    require_admin(actor,'payment.manage');order=lock(db,Order,order_id)
    receipt=_receipt(db,order,actor,body.amount_cents,body.reason)
    return ok({'id':receipt.id,'version':receipt.version,'status':receipt.status,'amountCents':receipt.amount_cents})


@router.get('/admin/receipts',response_model=Envelope[ReceiptList])
def receipts(status:str|None=None,db=Depends(get_db),actor:Actor=Depends(actor_dep)):
    require_admin(actor,'payment.manage')
    query=select(Receipt)
    if status:query=query.where(Receipt.status==status)
    rows=list(db.scalars(query.order_by(Receipt.created_at.desc()).limit(100)))
    refunds=dict(db.execute(select(ReceiptAdjustment.receipt_id,func.sum(ReceiptAdjustment.amount_cents))
        .where(ReceiptAdjustment.receipt_id.in_([r.id for r in rows])).group_by(ReceiptAdjustment.receipt_id)).all()) if rows else {}
    return ok({'items':[{'id':r.id,'orderId':r.order_id,'amountCents':r.amount_cents,
        'refundedCents':refunds.get(r.id,0),'refundableCents':r.amount_cents-refunds.get(r.id,0) if r.status=='UNMATCHED' else 0,
        'status':r.status,'version':r.version,'createdAt':r.created_at.isoformat()} for r in rows],'nextCursor':None})


@router.post('/admin/receipts/{receipt_id}/refund')
def refund_receipt(receipt_id:str,body:ReceiptRefundInput,db=Depends(get_db),actor:Actor=Depends(actor_dep)):
    require_admin(actor,'payment.manage');receipt=lock(db,Receipt,receipt_id,body.expected_version)
    if receipt.status!='UNMATCHED':raise DomainError('INVALID_TRANSITION','已分配收款须走订单售后退款',409)
    refunded=db.scalar(select(func.coalesce(func.sum(ReceiptAdjustment.amount_cents),0)).where(ReceiptAdjustment.receipt_id==receipt.id))
    if refunded+body.amount_cents>receipt.amount_cents:raise DomainError('REFUND_EXCEEDS_CASH','累计退款不得超过该笔实收',422)
    adjustment=ReceiptAdjustment(receipt_id=receipt.id,amount_cents=body.amount_cents,staff_id=actor.staff_id,reason=body.reason)
    db.add(adjustment)
    if refunded+body.amount_cents==receipt.amount_cents:receipt.status='REFUNDED'
    touch(receipt);audit(db,actor,'UNMATCHED_CASH_REFUND_RECORDED','RECEIPT',receipt.id,body.reason)
    return ok({'id':receipt.id,'version':receipt.version,'status':receipt.status,'refundedCents':refunded+body.amount_cents})


@router.get('/orders/{order_id}/history',response_model=Envelope[list[HistoryView]])
def history(order_id:str,db=Depends(get_db),actor:Actor=Depends(actor_dep)):
    order=db.get(Order,order_id);authorize_order(db,actor,order)
    return ok((order.data or {}).get('history',[]))


@router.get('/orders/{order_id}/proof',response_model=Envelope[ProofView|None])
def proof(order_id:str,db=Depends(get_db),actor:Actor=Depends(actor_dep)):
    order=db.get(Order,order_id);authorize_order(db,actor,order,'DELIVERY_PROOF_VIEW')
    return ok(order_view(db,order,actor)['proof'])


class DashboardView(DTO):
    order_count:int
    pending_payment_count:int
    delivered_count:int
    pending_print_task_count:int
    pending_binding_count:int
    pending_packing_count:int
    ready_for_delivery_count:int
    delivery_exception_count:int
    subtotal_cents:int
    coupon_discount_cents:int
    volume_discount_cents:int
    payable_cents:int
    cash_received_cents:int
    zero_payable_count:int
    refund_cents:int
    manual_adjustment_cents:int
    staff_discount_cents:int=0
    supplementary_fees_cents:int=0
    supplementary_cash_received_cents:int=0
    supplementary_refund_cents:int=0
    supplementary_outstanding_cents:int=0
    unmatched_receipt_cents:int=0
    unmatched_receipt_refund_cents:int=0


@router.get('/admin/dashboard',response_model=Envelope[DashboardView])
def dashboard(db=Depends(get_db),actor:Actor=Depends(actor_dep)):
    require_order_admin(actor)
    result={'orderCount':0,'pendingPaymentCount':0,'deliveredCount':0,'pendingPrintTaskCount':0,
        'pendingBindingCount':0,'pendingPackingCount':0,'readyForDeliveryCount':0,'deliveryExceptionCount':0,
        'subtotalCents':0,'couponDiscountCents':0,'volumeDiscountCents':0,'payableCents':0,'cashReceivedCents':0,
        'zeroPayableCount':0,'refundCents':0,'manualAdjustmentCents':0,'staffDiscountCents':0,
        'supplementaryFeesCents':0,'supplementaryCashReceivedCents':0,'supplementaryRefundCents':0,'supplementaryOutstandingCents':0,
        'unmatchedReceiptCents':0,'unmatchedReceiptRefundCents':0}
    for order in db.scalars(select(Order)):
        result['orderCount']+=1
        if not order.deleted_at:
            for state,key in [('PENDING_CONFIRMATION','pendingPaymentCount'),('DELIVERED','deliveredCount'),('PENDING_PRINT','pendingPackingCount'),('READY_FOR_DELIVERY','readyForDeliveryCount'),('DELIVERY_EXCEPTION','deliveryExceptionCount')]:
                if order.status==state:result[key]+=1
        if order.status!='CANCELLED' or order.settlement_kind:
            quote=db.get(Quote,order.quote_id)
            result['subtotalCents']+=quote.subtotal_cents
            result['payableCents']+=order.total_cents
            kind=(quote.data or {}).get('selectedPromotionType')
            if kind=='COUPON':result['couponDiscountCents']+=quote.discount_cents
            if kind=='VOLUME_TIER':result['volumeDiscountCents']+=quote.discount_cents
            if kind=='STAFF_GRADE':result['staffDiscountCents']+=quote.discount_cents
            result['cashReceivedCents']+=order.cash_received_cents
            result['refundCents']+=order.refunded_cents
            if order.settlement_kind=='ZERO_PAYABLE':result['zeroPayableCount']+=1
    result['pendingPrintTaskCount']=db.scalar(select(func.count()).select_from(PrintTask).where(PrintTask.active.is_(True),PrintTask.status.in_(['PENDING','ATTEMPTED','EXCEPTION'])))
    result['pendingBindingCount']=db.scalar(select(func.count()).select_from(BindingTask).where(BindingTask.status!='COMPLETED'))
    from .models import AfterSale,Refund
    result['manualAdjustmentCents']=sum((r.amount_cents-int((r.data or {}).get('suggestedCents',r.amount_cents))) for r in db.scalars(select(Refund)))
    from .models import SupplementaryCharge
    for charge in db.scalars(select(SupplementaryCharge)):
        result['supplementaryCashReceivedCents']+=charge.cash_received_cents
        result['supplementaryRefundCents']+=charge.refunded_cents
        if charge.status=='ACTIVE':
            result['supplementaryFeesCents']+=charge.amount_cents
            result['supplementaryOutstandingCents']+=max(0,charge.amount_cents-charge.cash_received_cents)
    # Unallocated cash remains a separate liability from confirmed order cash.
    # Preserve both gross receipt and refund facts even after it is fully repaid.
    unallocated=select(Receipt).where(Receipt.status.in_(('UNMATCHED','REFUNDED')),
        ~select(Payment.id).where(Payment.receipt_id==Receipt.id).exists())
    for receipt in db.scalars(unallocated):
        result['unmatchedReceiptCents']+=receipt.amount_cents
        result['unmatchedReceiptRefundCents']+=db.scalar(select(func.coalesce(func.sum(ReceiptAdjustment.amount_cents),0))
            .where(ReceiptAdjustment.receipt_id==receipt.id))
    for row in db.scalars(select(AnonymousLedger)):
        for key,value in row.totals.items():
            if key in result:result[key]+=value
    return ok(result)
