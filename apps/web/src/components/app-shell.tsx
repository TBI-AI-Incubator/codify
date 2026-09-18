import { TopBar, cn } from '../vendor/tbi-ui';
import {
  BookOpen,
  Copy,
  Globe2,
  Moon,
  Search as SearchIcon,
  Server,
  Sun,
  Upload,
} from 'lucide-react';
import { useTheme } from 'next-themes';
import { Link, Outlet, useLocation } from 'react-router';

import { JurisdictionSwitcher } from '@/components/jurisdiction-switcher';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarInset,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarTrigger,
} from '@/components/ui/sidebar';
import { useJurisdictionPin } from '@/lib/jurisdiction-pin';
import { parseJurisdictionPath, resolveCountry } from '@/lib/jurisdiction-path';

function AppSidebar() {
  const location = useLocation();
  const { pathname } = location;
  const { resolvedTheme, setTheme } = useTheme();
  const isDark = resolvedTheme === 'dark';
  const mcpUrl = `${window.location.origin}/mcp/`;
  const { jurisdictionPin, jurisdictions } = useJurisdictionPin();
  const currentCode =
    parseJurisdictionPath(pathname)?.code ??
    new URLSearchParams(location.search).get('jurisdiction') ??
    resolveCountry(jurisdictionPin, jurisdictions);
  return (
    <Sidebar collapsible="icon">
      <SidebarHeader className="flex h-12 items-center justify-center border-b p-0 px-3 group-data-[collapsible=icon]:px-0">
        <Link
          to="/"
          aria-label="Codify home"
          className="flex w-full items-center gap-2 group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:gap-0"
        >
          <img
            src="/tbi-logomark-on-light.svg"
            alt=""
            aria-hidden
            className="h-5 w-5 shrink-0 dark:hidden"
          />
          <img
            src="/tbi-logomark-on-dark.svg"
            alt=""
            aria-hidden
            className="hidden h-5 w-5 shrink-0 dark:block"
          />
          <span className="text-[0.9375rem] font-semibold tracking-tight text-ink group-data-[collapsible=icon]:hidden">
            Codify <span className="text-ink-60">Core</span>
          </span>
        </Link>
      </SidebarHeader>
      <JurisdictionSwitcher currentCode={currentCode} />
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupLabel>Corpus</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              <SidebarMenuItem>
                <SidebarMenuButton
                  isActive={pathname.startsWith('/search')}
                  render={<Link to={`/search?jurisdiction=${currentCode}`} />}
                  tooltip="Search"
                >
                  <SearchIcon className="h-4 w-4" />
                  <span>Search</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
              <SidebarMenuItem>
                <SidebarMenuButton
                  isActive={pathname === '/laws' || pathname.includes('/laws')}
                  render={<Link to={`/jurisdictions/${currentCode}/laws`} />}
                  tooltip="Laws"
                >
                  <BookOpen className="h-4 w-4" />
                  <span>Laws</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
              <SidebarMenuItem>
                <SidebarMenuButton
                  isActive={pathname.startsWith('/ingest')}
                  render={<Link to={`/ingest?jurisdiction=${currentCode}`} />}
                  tooltip="Ingest"
                >
                  <Upload className="h-4 w-4" />
                  <span>Ingest</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
        <SidebarGroup>
          <SidebarGroupLabel>Browse</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              <SidebarMenuItem>
                <SidebarMenuButton
                  isActive={pathname.startsWith('/jurisdictions')}
                  render={<Link to="/jurisdictions" />}
                  tooltip="Jurisdictions"
                >
                  <Globe2 className="h-4 w-4" />
                  <span>Jurisdictions</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>
      <SidebarFooter className="border-t border-ink-20 p-2">
        <Dialog>
          <DialogTrigger
            title="MCP server"
            className="flex h-8 w-full items-center gap-2 rounded-md px-2 text-sm text-ink-60 transition-colors hover:bg-paper-sunken hover:text-ink group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:px-0"
          >
            <Server className="h-4 w-4" aria-hidden />
            <span className="group-data-[collapsible=icon]:hidden">MCP</span>
          </DialogTrigger>
          <DialogContent className="sm:max-w-md">
            <DialogHeader>
              <DialogTitle className="text-ink">Connect an MCP client</DialogTitle>
              <DialogDescription>Use this URL in your MCP client configuration.</DialogDescription>
            </DialogHeader>
            <div className="flex items-center gap-2 rounded-md border border-ink-20 bg-paper-sunken p-2">
              <code className="min-w-0 flex-1 truncate font-tbi-mono text-xs text-ink">
                {mcpUrl}
              </code>
              <button
                type="button"
                onClick={() => void navigator.clipboard.writeText(mcpUrl)}
                title="Copy MCP server URL"
                aria-label="Copy MCP server URL"
                className="grid size-7 shrink-0 place-items-center rounded-sm text-ink-60 transition-colors hover:bg-paper hover:text-ink"
              >
                <Copy className="size-3.5" aria-hidden />
              </button>
            </div>
          </DialogContent>
        </Dialog>
        <label
          title="Toggle light and dark mode"
          className="flex h-8 w-full cursor-pointer items-center justify-center gap-2 rounded-md px-2 text-xs font-medium transition-colors hover:bg-paper-sunken group-data-[collapsible=icon]:px-0"
        >
          <input
            type="checkbox"
            checked={isDark}
            onChange={() => setTheme(isDark ? 'light' : 'dark')}
            aria-label="Dark mode"
            className="peer sr-only"
          />
          <span
            className={`flex items-center gap-1 group-data-[collapsible=icon]:hidden ${
              isDark ? 'text-ink-40' : 'text-ink'
            }`}
          >
            <Sun className="h-3.5 w-3.5" aria-hidden />
            Light mode
          </span>
          <span
            aria-hidden="true"
            className="relative h-5 w-9 shrink-0 rounded-full bg-ink-20 transition-colors peer-checked:bg-emphasis-strong after:absolute after:inset-s-0.5 after:top-0.5 after:h-4 after:w-4 after:rounded-full after:bg-paper after:transition-transform peer-checked:after:translate-x-4"
          />
          <span
            className={`flex items-center gap-1 group-data-[collapsible=icon]:hidden ${
              isDark ? 'text-ink' : 'text-ink-40'
            }`}
          >
            <Moon className="h-3.5 w-3.5" aria-hidden />
            Dark mode
          </span>
        </label>
      </SidebarFooter>
    </Sidebar>
  );
}

function TopBarShell() {
  return (
    <TopBar
      className="z-50 h-12 bg-paper"
      breadcrumb={
        <div className="flex min-w-0 items-center gap-2">
          <SidebarTrigger className="-ms-1 text-ink-60 hover:text-ink" />
        </div>
      }
    />
  );
}

export default function AppShell() {
  return (
    <SidebarProvider defaultOpen={true}>
      <AppSidebar />
      <SidebarInset className={cn('flex min-h-svh flex-col')}>
        <TopBarShell />
        <main
          id="main"
          tabIndex={-1}
          className="relative z-0 isolate flex-1 overflow-auto outline-none"
        >
          <Outlet />
        </main>
      </SidebarInset>
    </SidebarProvider>
  );
}
