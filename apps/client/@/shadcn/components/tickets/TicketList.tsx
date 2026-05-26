import { cn } from "@/shadcn/lib/utils";
import { Command, CommandGroup, CommandItem, CommandList } from "@/shadcn/ui/command";
import { ContextMenu, ContextMenuContent, ContextMenuItem, ContextMenuSeparator, ContextMenuSub, ContextMenuSubContent, ContextMenuSubTrigger, ContextMenuTrigger } from '@/shadcn/ui/context-menu';
import { CheckIcon } from 'lucide-react';
import moment from 'moment';
import Link from 'next/link';
import { Ticket, UISettings } from '../../types/tickets';

interface TicketListProps {
  tickets: Ticket[];
  onStatusChange: (ticket: Ticket) => void;
  onAssigneeChange: (ticketId: string, user?: any) => void;
  onPriorityChange: (ticket: Ticket, priority: string) => void;
  onDelete?: (ticketId: string) => void;
  users: any[];
  currentUser: any;
  uiSettings: UISettings;
}

const priorities = ["low", "medium", "high"];

export default function TicketList({
  tickets,
  onStatusChange,
  onAssigneeChange,
  onPriorityChange,
  onDelete,
  users,
  currentUser,
  uiSettings
}: TicketListProps) {
  const priorityColors: Record<string, string> = {
    low: "bg-blue-100 text-blue-800",
    medium: "bg-green-100 text-green-800",
    high: "bg-red-100 text-red-800",
  };

  return (
    <div className="flex-1 overflow-y-auto">
      {tickets.map((ticket) => {
        const badge = priorityColors[ticket.priority?.toLowerCase()] ?? "bg-gray-100 text-gray-800";

        return (
          <ContextMenu key={ticket.id}>
            <ContextMenuTrigger>
              <Link href={`/issue/${ticket.id}`}>
                <div className="flex flex-row w-full bg-white dark:bg-[#0A090C] dark:hover:bg-green-600 border-b-[1px] p-1.5 justify-between px-6 hover:bg-gray-100">
                  <div className="flex flex-row items-center space-x-4">
                    {uiSettings.showTicketNumbers && (
                      <span className="text-xs font-semibold">#{ticket.Number}</span>
                    )}
                    <span className="text-xs font-semibold">{ticket.title}</span>
                  </div>
                  <div className="flex flex-row space-x-3 items-center">
                    {uiSettings.showDates && (
                      <span className="text-xs">{moment(ticket.createdAt).format("DD/MM/yyyy")}</span>
                    )}
                    {uiSettings.showType && (
                      <span className="inline-flex items-center rounded-md px-2 py-1 capitalize justify-center w-20 text-xs font-medium ring-1 ring-inset ring-gray-500/10 bg-orange-400 text-white">
                        {ticket.type}
                      </span>
                    )}
                    {/* Open/closed status badge — always shown */}
                    {ticket.isComplete ? (
                      <span className="inline-flex items-center gap-x-1.5 rounded-md bg-red-100 px-2 w-20 justify-center py-1 text-xs ring-1 ring-inset ring-gray-500/10 font-medium text-red-700">
                        <svg className="h-1.5 w-1.5 fill-red-500" viewBox="0 0 6 6" aria-hidden="true"><circle cx={3} cy={3} r={3} /></svg>
                        Closed
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-x-1.5 rounded-md bg-green-100 px-2 w-20 justify-center py-1 text-xs ring-1 ring-inset ring-gray-500/10 font-medium text-green-700">
                        <svg className="h-1.5 w-1.5 fill-green-500" viewBox="0 0 6 6" aria-hidden="true"><circle cx={3} cy={3} r={3} /></svg>
                        Open
                      </span>
                    )}
                    {/* RCA'd badge */}
                    {ticket.isAnalysed && (
                      <span className="inline-flex items-center gap-x-1.5 rounded-md bg-blue-100 px-2 py-1 text-xs ring-1 ring-inset ring-gray-500/10 font-medium text-blue-700">
                        <svg className="h-1.5 w-1.5 fill-blue-500" viewBox="0 0 6 6" aria-hidden="true"><circle cx={3} cy={3} r={3} /></svg>
                        RCA&apos;d
                      </span>
                    )}
                    {uiSettings.showPriority && (
                      <span className={`inline-flex items-center rounded-md px-2 py-1 capitalize justify-center w-20 text-xs font-medium ring-1 ring-inset ring-gray-500/10 ${badge}`}>
                        {ticket.priority}
                      </span>
                    )}
                    {uiSettings.showAvatars && (
                      <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-gray-500">
                        <span className="text-[11px] font-medium leading-none text-white uppercase">
                          {ticket.assignedTo ? ticket.assignedTo.name[0] : ""}
                        </span>
                      </span>
                    )}
                  </div>
                </div>
              </Link>
            </ContextMenuTrigger>
            <ContextMenuContent className="w-52">
              <ContextMenuItem onClick={() => onStatusChange(ticket)}>
                {ticket.isComplete ? "Re-open Issue" : "Close Issue"}
              </ContextMenuItem>
              <ContextMenuSeparator />
              <ContextMenuSub>
                <ContextMenuSubTrigger>Assign To</ContextMenuSubTrigger>
                <ContextMenuSubContent className="w-64 ml-1">
                  <Command>
                    <CommandList>
                      <CommandGroup heading="Assigned To">
                        <CommandItem onSelect={() => onAssigneeChange(ticket.id, undefined)}>
                          <div className={cn("mr-2 flex h-4 w-4 items-center justify-center rounded-sm border border-primary", !ticket.assignedTo ? "bg-primary text-primary-foreground" : "opacity-50 [&_svg]:invisible")}>
                            <CheckIcon className="h-4 w-4" />
                          </div>
                          <span>Unassigned</span>
                        </CommandItem>
                        {users?.map((u) => (
                          <CommandItem key={u.id} onSelect={() => onAssigneeChange(ticket.id, u)}>
                            <div className={cn("mr-2 flex h-4 w-4 items-center justify-center rounded-sm border border-primary", ticket.assignedTo?.name === u.name ? "bg-primary text-primary-foreground" : "opacity-50 [&_svg]:invisible")}>
                              <CheckIcon className="h-4 w-4" />
                            </div>
                            <span>{u.name}</span>
                          </CommandItem>
                        ))}
                      </CommandGroup>
                    </CommandList>
                  </Command>
                </ContextMenuSubContent>
              </ContextMenuSub>
              <ContextMenuSub>
                <ContextMenuSubTrigger>Change Priority</ContextMenuSubTrigger>
                <ContextMenuSubContent className="w-48 ml-1">
                  <Command>
                    <CommandList>
                      <CommandGroup heading="Priority">
                        {priorities.map((p) => (
                          <CommandItem key={p} onSelect={() => onPriorityChange(ticket, p)}>
                            <div className={cn("mr-2 flex h-4 w-4 items-center justify-center rounded-sm border border-primary", ticket.priority?.toLowerCase() === p ? "bg-primary text-primary-foreground" : "opacity-50 [&_svg]:invisible")}>
                              <CheckIcon className="h-4 w-4" />
                            </div>
                            <span className="capitalize">{p}</span>
                          </CommandItem>
                        ))}
                      </CommandGroup>
                    </CommandList>
                  </Command>
                </ContextMenuSubContent>
              </ContextMenuSub>
              <ContextMenuSeparator />
              <ContextMenuItem onClick={() => {
                navigator.clipboard.writeText(`${window.location.origin}/issue/${ticket.id}`);
              }}>
                Share Link
              </ContextMenuItem>
              {onDelete && (
                <>
                  <ContextMenuSeparator />
                  <ContextMenuItem
                    className="text-red-600"
                    onClick={() => {
                      if (confirm("Are you sure you want to delete this ticket?")) {
                        onDelete(ticket.id);
                      }
                    }}
                  >
                    Delete Ticket
                  </ContextMenuItem>
                </>
              )}
            </ContextMenuContent>
          </ContextMenu>
        );
      })}
    </div>
  );
}
