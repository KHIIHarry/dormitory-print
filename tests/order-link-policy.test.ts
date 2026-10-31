import {expect,it} from 'vitest';
import {linkRequest,READ_ONLY_LINK_SCOPES} from '../src/growth/link-policy';
it('new order links default to read-only with opt-in stage permissions',()=>{
 expect(linkRequest('客户保存',[]).scopes).toEqual(READ_ONLY_LINK_SCOPES);
 expect(linkRequest('客户确认允许修改',['ORDER_REVISE_PENDING']).scopes).toContain('ORDER_REVISE_PENDING');
 expect(()=>linkRequest('客户保存',['ACCOUNT_ADMIN'])).toThrow();
});
it('lost-link replacement explicitly revokes prior grants and requires a reason',()=>{
 expect(linkRequest('人工核验补发',[],undefined,true).reissue).toBe(true);
 expect(()=>linkRequest('   ',[])).toThrow();
});
