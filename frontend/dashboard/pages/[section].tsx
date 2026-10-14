import { useRouter } from "next/router";
import Marketing from "../lib/Marketing";
import IdentityPage from "../lib/IdentityPage";
import WorkspacePage from "../lib/WorkspacePage";
const publicPages=["pricing","features","guides","integrations-guide","support","status"];
const workspacePages=["applications","onboarding","integrations","agents","logs","usage","billing","team","notifications","settings"];
export default function Page(){const router=useRouter();const page=String(router.query.section||"");if(!router.isReady)return null;if(["forgot","reset","verify","mfa-setup"].includes(page))return <IdentityPage key={page} page={page}/>;if(publicPages.includes(page))return <Marketing page={page}/>;if(workspacePages.includes(page))return <WorkspacePage key={page} page={page}/>;return <div className="empty">Page not found.</div>}
