import {useEffect,useRef,useState} from 'react';
import {Button,CopyButton,Field,Modal,Notice,Panel} from '../components/ui';
import {LinkQrButton} from '../components/link-qr';
import {useStore} from '../lib/store';
import {useIdentity} from './identity';
import {growthRequest,useGrowthResource} from './client';
import {orderAccessUrl} from './order-security';
import {linkRequest,OPTIONAL_LINK_SCOPES} from './link-policy';
import type {AccessLink} from './types';
export function CustomerSavedLink({orderId}:{orderId:string}){
 const {savedAccessLinks,toast}=useStore();const link=savedAccessLinks[orderId];
 return <Panel title="保存订单访问方式">{link?<><Notice>链接和二维码均包含本单访问权限，请私下保存。关闭当前会话后无法重新读取原凭据。</Notice><div className="button-row"><CopyButton text={link} label="复制专属链接"/><LinkQrButton value={link} label="订单二维码" sensitive/>{!!navigator.share&&<Button variant="secondary" onClick={async()=>{try{await navigator.share({title:'我的宿舍打印订单',url:link})}catch(e){if((e as Error).name!=='AbortError')toast('分享未完成，请复制链接')}}}>保存或分享</Button>}</div></>:<p className="form-hint">可使用当前会话查看订单；跨设备访问请联系管理员签发专属链接。短提取码不能单独查看私有文件。</p>}</Panel>
}
export function AdminAccessLinks({orderId}:{orderId:string}){
 const {staff,generation}=useIdentity();const {toast}=useStore();
 const allowed=!!staff&&!staff.mustChangePassword&&((staff.capabilities??[]).includes('*')||(staff.capabilities??[]).includes('order.access-link.manage'));
 const resource=useGrowthResource<{items:AccessLink[]}>(allowed?`/admin/orders/${orderId}/access-links`:null);
 const [action,setAction]=useState<'issue'|'reissue'|AccessLink|null>(null);
 const [reason,setReason]=useState(''),[expiry,setExpiry]=useState(''),[scopes,setScopes]=useState<string[]>([]);
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[issued,setIssued]=useState('');
 const current=useRef({generation,orderId,allowed});current.current={generation,orderId,allowed};
 const running=useRef(false),epoch=useRef(0);
 useEffect(()=>{epoch.current++;running.current=false;setBusy(false);setIssued('');setAction(null);setReason('');setError('');setScopes([])},[generation,orderId,allowed]);
 if(!allowed)return null;
 const open=(next:'issue'|'reissue'|AccessLink)=>{setAction(next);setReason('');setError('');setExpiry('');setScopes([])};
 const submit=async()=>{
  if(running.current)return;
  if(!reason.trim()){setError('请填写操作原因');return}
  running.current=true;setBusy(true);setError('');const origin={generation,orderId,epoch:epoch.current};
  const stillCurrent=()=>epoch.current===origin.epoch&&current.current.generation===origin.generation&&current.current.orderId===origin.orderId&&current.current.allowed;
  try{
   if(typeof action==='string'){
    const body=linkRequest(reason,scopes,expiry?new Date(expiry).toISOString():undefined,action==='reissue');
    const result=await growthRequest<{accessToken:string;grant?:AccessLink}>(`/admin/orders/${orderId}/access-links${action==='reissue'?'/reissue':''}`,{method:'POST',body});
    if(!stillCurrent())return;
    setIssued(orderAccessUrl(location.origin,orderId,result.accessToken));
   }else if(action){
    await growthRequest(`/admin/orders/${orderId}/access-links/${action.id}/revoke`,{method:'POST',body:{expectedVersion:action.version,reason:reason.trim()}});
    if(!stillCurrent())return;
    setIssued('');toast('授权及其派生导入权限已撤销');
   }
   if(stillCurrent()){setAction(null);setReason('');await resource.reload()}
  }catch(e){if(stillCurrent())setError((e as Error).message)}finally{if(stillCurrent()){running.current=false;setBusy(false)}}
 };
 const signing=typeof action==='string';
 return <Panel className="order-access-panel" title="订单专属链接" action={<div className="button-row"><Button variant="secondary" onClick={()=>open('issue')}>签发新链接</Button><Button variant="secondary" onClick={()=>open('reissue')}>丢失后补发</Button></div>}>
 <p className="form-hint">默认只读查询与授权预览。新增链接不撤销旧链接；丢失补发会撤销该单旧链接及派生导入权限，真实所有者账号权限保留。</p>
 {issued&&<div className="notice"><strong>本次新签发 · 仅显示一次</strong><p>请私下保存或交付此链接及二维码。</p><div className="button-row"><CopyButton text={issued} label="复制专属链接"/><LinkQrButton value={issued} label="订单二维码" sensitive/><CopyButton text={`这份宿舍打印订单可以通过专属链接查看：${issued}\n链接包含本单访问权限，请勿公开转发。`} label="复制微信文案"/><Button variant="secondary" onClick={()=>setIssued('')}>已交付，关闭凭据</Button></div></div>}
 {resource.loading?<p>正在加载授权记录…</p>:resource.error?<Notice type="error">{resource.error}<Button variant="secondary" onClick={()=>resource.reload()}>重试</Button></Notice>:resource.data?.items.length?<div className="table-scroll"><table><thead><tr><th>签发时间 / 操作人</th><th>有效期</th><th>状态</th><th>操作</th></tr></thead><tbody>{resource.data.items.map(link=><tr key={link.id}><td>{new Date(link.createdAt).toLocaleString('zh-CN')}<small>{link.createdBy}</small></td><td>{link.expiresAt?new Date(link.expiresAt).toLocaleString('zh-CN'):'未返回期限，请核实授权'}</td><td>{link.revokedAt?'已撤销':link.expiresAt&&Date.parse(link.expiresAt)<Date.now()?'已过期':'有效'}</td><td>{!link.revokedAt&&<button className="text-button" onClick={()=>open(link)}>撤销授权</button>}</td></tr>)}</tbody></table></div>:<Notice>尚未签发专属链接，历史凭据不会再次显示。</Notice>}
 {action&&<Modal title={signing?action==='reissue'?'核验后补发链接':'签发单订单访问链接':'撤销单订单授权'} onClose={()=>{if(!busy)setAction(null)}}>
 <Notice>{signing?action==='reissue'?'确认已人工核验客户。补发将撤销该单全部旧链接和由其导入的授权，独立所有者权限保留。':'默认只读查询及有权预览。只有明确勾选才附加对应阶段的编辑权限。':'撤销后该链接及其派生导入权限失效，原单履约与所有权保留。'}</Notice>
 {signing&&<><Field label="到期时间（留空默认30天）"><input type="datetime-local" value={expiry} onChange={e=>setExpiry(e.target.value)}/></Field><fieldset><legend>额外授权（可不选）</legend>{OPTIONAL_LINK_SCOPES.map(([scope,label])=><label className="checkbox-label" key={scope}><input type="checkbox" checked={scopes.includes(scope)} onChange={e=>setScopes(current=>e.target.checked?[...current,scope]:current.filter(s=>s!==scope))}/>{label}</label>)}</fieldset></>}
 <Field label="操作原因"><textarea value={reason} onChange={e=>setReason(e.target.value)} maxLength={500}/></Field>{error&&<p role="alert" className="field-error">{error}</p>}<Button disabled={busy} onClick={submit}>{busy?'正在提交…':'确认操作'}</Button></Modal>}
 </Panel>
}
