import { Button, CopyButton, Notice, Panel } from "../components/ui";
import { LinkQrButton } from "../components/link-qr";
import { useStore } from "../lib/store";
import { useIdentity } from "./identity";
export function CustomerSavedLink({ orderId }: { orderId: string }) {
  const { savedAccessLinks, toast, data } = useStore();
  const order = data.orders.find((item) => item.id === orderId);
  const { customer } = useIdentity();
  if (
    order?.accessKind === "OWNER" ||
    (order?.ownerAccountId && order.ownerAccountId === customer.account?.id)
  )
    return (
      <Panel title="账号订单">
        <p>可在「我的订单」长期查看订单记录。</p>
      </Panel>
    );
  const link = savedAccessLinks[orderId];
  return (
    <Panel title="保存订单访问方式">
      {link ? (
        <>
          <Notice>
            游客订单链接默认有效 14
            天。链接和二维码包含本单只读访问权限，请私下保存。
          </Notice>
          <div className="button-row">
            <CopyButton text={link} label="复制专属链接" />
            <LinkQrButton value={link} label="订单二维码" sensitive />
            {!!navigator.share && (
              <Button
                variant="secondary"
                onClick={async () => {
                  try {
                    await navigator.share({
                      title: "我的宿舍打印订单",
                      url: link,
                    });
                  } catch (e) {
                    if ((e as Error).name !== "AbortError")
                      toast("分享未完成，请复制链接", "warning");
                  }
                }}
              >
                保存或分享
              </Button>
            )}
          </div>
        </>
      ) : (
        <p className="form-hint">
          可使用当前会话查看订单；跨设备访问请联系管理员签发专属链接。短提取码不能单独查看私有文件。
        </p>
      )}
    </Panel>
  );
}
