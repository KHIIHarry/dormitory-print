import { useState } from "react";
import { Button, Modal } from "../components/ui";
import { pageAssignments, pageSelectionLabel } from "../lib/print-settings";
import type { PrintFile } from "../lib/types";
import "./skip-pages-picker.css";

const PAGE_SIZE = 60;

export function SkipPagesPicker({
  file,
  onConfirm,
  onClose,
}: {
  file: PrintFile;
  onConfirm: (pages: number[]) => void;
  onClose: () => void;
}) {
  const assigned = pageAssignments(file);
  const available = Array.from(
    { length: file.pages },
    (_, index) => index + 1,
  ).filter((page) => !assigned.has(page));
  const [selected, setSelected] = useState(
    () =>
      new Set(file.excludedPages.filter((page) => available.includes(page))),
  );
  const [batch, setBatch] = useState(0);
  const start = batch * PAGE_SIZE + 1;
  const end = Math.min(file.pages, start + PAGE_SIZE - 1);
  const pages = Array.from(
    { length: end - start + 1 },
    (_, index) => start + index,
  );

  return (
    <Modal title="选择跳过打印页" onClose={onClose}>
      <div className="skip-pages-picker">
        <p className="skip-pages-help" id="skip-pages-help">
          共 {file.pages}{" "}
          页。已分配到打印分段的页面不可跳过；其余页面请添加分段或勾选跳过。
        </p>
        <div className="skip-pages-tools">
          <Button
            variant="secondary"
            disabled={!available.length}
            onClick={() => setSelected(new Set(available))}
          >
            选择全部未分配页
          </Button>
          <Button
            variant="text"
            disabled={!selected.size}
            onClick={() => setSelected(new Set())}
          >
            清空选择
          </Button>
        </div>
        <div
          className="skip-pages-grid"
          role="group"
          aria-label="页面列表"
          aria-describedby="skip-pages-help"
        >
          {pages.map((page) => {
            const segments = assigned.get(page);
            const status = segments
              ? `已在分段 ${segments.join("、")} 中打印`
              : "";
            return (
              <label
                key={page}
                className={`skip-page-option${segments ? " allocated" : ""}`}
              >
                <input
                  type="checkbox"
                  aria-label={`第 ${page} 页${status ? `，${status}` : ""}`}
                  disabled={!!segments}
                  checked={!segments && selected.has(page)}
                  onChange={(event) => {
                    const checked = event.target.checked;
                    setSelected((current) => {
                      const next = new Set(current);
                      if (checked) next.add(page);
                      else next.delete(page);
                      return next;
                    });
                  }}
                />
                <span>
                  <strong>第 {page} 页</strong>
                  {status && <small>{status}</small>}
                </span>
              </label>
            );
          })}
        </div>
        {file.pages > PAGE_SIZE && (
          <div className="skip-pages-pagination" aria-label="页码列表分页">
            <Button
              variant="secondary"
              disabled={batch === 0}
              onClick={() => setBatch(batch - 1)}
            >
              上一组
            </Button>
            <span>
              {start}–{end} / {file.pages} 页
            </span>
            <Button
              variant="secondary"
              disabled={end === file.pages}
              onClick={() => setBatch(batch + 1)}
            >
              下一组
            </Button>
          </div>
        )}
        <p className="skip-pages-selection" role="status">
          {selected.size
            ? `已选择 ${selected.size} 页：${pageSelectionLabel([...selected])}`
            : "尚未选择跳过页"}
        </p>
        <div className="skip-pages-actions">
          <Button variant="secondary" onClick={onClose}>
            取消
          </Button>
          <Button
            onClick={() =>
              onConfirm(
                [...selected]
                  .filter((page) => !assigned.has(page))
                  .sort((a, b) => a - b),
              )
            }
          >
            确认跳过
          </Button>
        </div>
      </div>
    </Modal>
  );
}
