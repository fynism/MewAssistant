import {
  ArrowDown, ArrowLeft, ArrowRight, ChatDotRound, Clock, Close, CopyDocument, Document,
  Download, Files, Fold, Folder, Key, Loading, Lock, Moon, MoreFilled, Operation, Plus,
  QuestionFilled, Reading, Search, Setting, Sunny, SwitchButton, Upload, User, Warning,
} from '@element-plus/icons-vue';
import { ElAlert } from 'element-plus/es/components/alert/index.mjs';
import { ElButton } from 'element-plus/es/components/button/index.mjs';
import { ElCollapse } from 'element-plus/es/components/collapse/index.mjs';
import { ElConfigProvider } from 'element-plus/es/components/config-provider/index.mjs';
import { ElDatePicker } from 'element-plus/es/components/date-picker/index.mjs';
import { ElDialog } from 'element-plus/es/components/dialog/index.mjs';
import { ElDrawer } from 'element-plus/es/components/drawer/index.mjs';
import { ElDropdown } from 'element-plus/es/components/dropdown/index.mjs';
import { ElForm } from 'element-plus/es/components/form/index.mjs';
import { ElIcon } from 'element-plus/es/components/icon/index.mjs';
import { ElInput } from 'element-plus/es/components/input/index.mjs';
import { ElInputNumber } from 'element-plus/es/components/input-number/index.mjs';
import { ElLoading } from 'element-plus/es/components/loading/index.mjs';
import { ElMessage } from 'element-plus/es/components/message/index.mjs';
import { ElMessageBox } from 'element-plus/es/components/message-box/index.mjs';
import { ElSelect } from 'element-plus/es/components/select/index.mjs';
import { ElTable } from 'element-plus/es/components/table/index.mjs';
import { ElTabs } from 'element-plus/es/components/tabs/index.mjs';
import { ElTooltip } from 'element-plus/es/components/tooltip/index.mjs';
import locale from 'element-plus/es/locale/lang/zh-cn';
import 'element-plus/es/components/alert/style/css';
import 'element-plus/es/components/button/style/css';
import 'element-plus/es/components/collapse/style/css';
import 'element-plus/es/components/date-picker/style/css';
import 'element-plus/es/components/dialog/style/css';
import 'element-plus/es/components/drawer/style/css';
import 'element-plus/es/components/dropdown/style/css';
import 'element-plus/es/components/form/style/css';
import 'element-plus/es/components/icon/style/css';
import 'element-plus/es/components/input/style/css';
import 'element-plus/es/components/input-number/style/css';
import 'element-plus/es/components/loading/style/css';
import 'element-plus/es/components/message/style/css';
import 'element-plus/es/components/message-box/style/css';
import 'element-plus/es/components/select/style/css';
import 'element-plus/es/components/table/style/css';
import 'element-plus/es/components/tabs/style/css';
import 'element-plus/es/components/tooltip/style/css';

export { ElIcon, ElMessage, ElMessageBox, locale };
export const icons = {
  ArrowDown, ArrowLeft, ArrowRight, ChatDotRound, Clock, Close, CopyDocument, Document,
  Download, Files, Fold, Folder, Key, Loading, Lock, Moon, MoreFilled, Operation, Plus,
  QuestionFilled, Reading, Search, Setting, Sunny, SwitchButton, Upload, User, Warning,
};

export function install(app) {
  // Parent plugins also register their public children, such as ElSelect's ElOption.
  for (const plugin of [
    ElAlert, ElButton, ElCollapse, ElConfigProvider, ElDatePicker, ElDialog, ElDrawer,
    ElDropdown, ElForm, ElInput, ElInputNumber, ElLoading, ElMessage, ElMessageBox,
    ElSelect, ElTable, ElTabs, ElTooltip,
  ]) {
    app.use(plugin);
  }
}
