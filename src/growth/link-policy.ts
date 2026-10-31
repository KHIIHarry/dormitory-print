export const READ_ONLY_LINK_SCOPES = ['ORDER_VIEW','FINAL_PDF_PREVIEW','DELIVERY_PROOF_VIEW'] as const;
export const OPTIONAL_LINK_SCOPES = [
 ['ORDER_REVISE_PENDING','待确认时编辑订单'],
 ['ORDER_CANCEL_PENDING','待确认时撤销订单'],
 ['ORDER_CHANGE_LOCATION_PRE_DELIVERY','配送开始前修改楼栋'],
 ['ORDER_IMPORT','将本单导入客户账号'],
] as const;
export function linkRequest(reason:string,extras:readonly string[],expiresAt?:string,reissue=false) {
 const clean=reason.trim();
 if(!clean)throw new Error('请填写操作原因');
 const allowed=new Set<string>(OPTIONAL_LINK_SCOPES.map(([scope])=>scope));
 if(extras.some(scope=>!allowed.has(scope)))throw new Error('包含未授权的链接权限');
 return {reason:clean,scopes:[...READ_ONLY_LINK_SCOPES,...new Set(extras)],...(expiresAt?{expiresAt}:{}),reissue};
}
