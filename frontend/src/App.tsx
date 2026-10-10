import { lazy, Suspense } from "react";
import { NavLink, Route, Routes, useLocation } from "react-router-dom";
import { AccountsPage, CommunitiesPage, DashboardPage } from "./catalog";
import { CategoryGendersPage } from "./category-genders";
import { GridDetailPage, GridImportPage, GridsPage } from "./grids";
import { MediaPage } from "./media";
import { ActivityProvider, ActivityIndicator, ActivityPage } from "./activity";
import { CommunityDetailPage } from "./community";
import {
  CampaignDetailPage,
  CampaignNewPage,
  CampaignsPage,
} from "./campaigns";
const VkAuthHelper =
  import.meta.env.VITE_ENABLE_VK_AUTH_HELPER === "true"
    ? lazy(() => import("./dev/VkAuthHelper"))
    : null;
import { PhotoValidationPage } from "./validation";
export function App() {
  const location = useLocation();
  if (location.pathname.startsWith("/review/"))
    return (
      <Routes>
        <Route path="/review/:batchId" element={<PhotoValidationPage />} />
      </Routes>
    );
  return (
    <ActivityProvider>
      <div className="app-shell">
        <aside>
          <NavLink className="brand" to="/">
            DropGrid<span>Campaign workspace</span>
          </NavLink>
          <nav aria-label="Main navigation">
            <NavLink to="/" end>
              Dashboard
            </NavLink>
            <NavLink to="/campaigns">Campaigns</NavLink>
            <NavLink to="/grids">Grids</NavLink>
            <NavLink to="/categories">Categories</NavLink>
            <NavLink to="/communities">Communities</NavLink>
            <NavLink to="/accounts">Accounts</NavLink>
            <NavLink to="/media">Media</NavLink>
            <NavLink to="/activity">Activity Center</NavLink>
          </nav>
          <p className="sidebar-note">
            Preparation mode
            <br />
            VK sending is disabled
          </p>
        </aside>
        <main>
          <header className="workspace-status">
            <ActivityIndicator />
          </header>
          <Routes>
            {VkAuthHelper &&
              ["/dev/vk-auth", "/dev/vk-auth/copy"].map((path) => (
                <Route
                  key={path}
                  path={path}
                  element={
                    <Suspense fallback={<p>Loading helper…</p>}>
                      <VkAuthHelper />
                    </Suspense>
                  }
                />
              ))}
            <Route path="/activity" element={<ActivityPage />} />
            <Route path="/" element={<DashboardPage />} />
            <Route path="/grids" element={<GridsPage />} />
            <Route path="/grids/new" element={<GridImportPage />} />
            <Route path="/grids/:id" element={<GridDetailPage />} />
            <Route path="/categories" element={<CategoryGendersPage />} />
            <Route path="/campaigns" element={<CampaignsPage />} />
            <Route path="/campaigns/new" element={<CampaignNewPage />} />
            <Route path="/campaigns/:id" element={<CampaignDetailPage />} />
            <Route path="/accounts" element={<AccountsPage />} />
            <Route path="/communities" element={<CommunitiesPage />} />
            <Route path="/communities/:id" element={<CommunityDetailPage />} />
            <Route path="/media" element={<MediaPage />} />
            <Route
              path="*"
              element={
                <>
                  <h1>Страница не найдена</h1>
                  <NavLink to="/">Dashboard</NavLink>
                </>
              }
            />
          </Routes>
        </main>
      </div>
    </ActivityProvider>
  );
}
