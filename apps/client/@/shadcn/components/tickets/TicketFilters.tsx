import { cn } from "@/shadcn/lib/utils";
import { Button } from "@/shadcn/ui/button";
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList, CommandSeparator } from "@/shadcn/ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "@/shadcn/ui/popover";
import { CheckIcon, Filter } from "lucide-react";
import { useState } from "react";
import FilterBadge from "./FilterBadge";

interface TicketFiltersProps {
  selectedPriorities: string[];
  selectedStatuses: string[];
  selectedAssignees: string[];
  selectedTypes: string[];
  tickets?: any[];
  users: any[];
  onPriorityToggle: (priority: string) => void;
  onStatusToggle: (status: string) => void;
  onAssigneeToggle: (assignee: string) => void;
  onTypeToggle: (type: string) => void;
  onClearFilters: () => void;
}

type FilterType = "priority" | "status" | "assignee" | "type" | null;

const TICKET_TYPES = ["bug", "feature", "support", "incident", "service", "maintenance", "access", "feedback"];

export default function TicketFilters({
  selectedPriorities,
  selectedStatuses,
  selectedAssignees,
  selectedTypes,
  tickets = [],
  users,
  onPriorityToggle,
  onStatusToggle,
  onAssigneeToggle,
  onTypeToggle,
  onClearFilters,
}: TicketFiltersProps) {
  const [activeFilter, setActiveFilter] = useState<FilterType>(null);
  const [filterSearch, setFilterSearch] = useState("");

  const priorities = ["low", "medium", "high"];
  const statuses = ["open", "closed"];
  const assignees = ["Unassigned", ...users.map(u => u.name)].filter(
    (name, i, self) => self.indexOf(name) === i
  );

  const filtered = <T extends string>(list: T[]) =>
    list.filter((item) => item.toLowerCase().includes(filterSearch.toLowerCase()));

  const back = () => { setActiveFilter(null); setFilterSearch(""); };

  const hasFilters =
    selectedPriorities.length > 0 ||
    selectedStatuses.length > 0 ||
    selectedAssignees.length > 0 ||
    selectedTypes.length > 0;

  return (
    <div className="flex flex-row items-center gap-2">
      <Popover>
        <PopoverTrigger asChild>
          <Button variant="ghost" size="sm" className="h-6 bg-transparent">
            <Filter className="mr-2 h-4 w-4" />
            <span className="hidden sm:block">Filters</span>
          </Button>
        </PopoverTrigger>
        <PopoverContent className="w-[300px] p-0" align="start">
          {!activeFilter ? (
            <Command>
              <CommandInput placeholder="Search filters..." value={filterSearch} onValueChange={setFilterSearch} />
              <CommandList>
                <CommandEmpty>No results found.</CommandEmpty>
                <CommandGroup>
                  <CommandItem onSelect={() => { setActiveFilter("priority"); setFilterSearch(""); }}>Priority</CommandItem>
                  <CommandItem onSelect={() => { setActiveFilter("status"); setFilterSearch(""); }}>Status</CommandItem>
                  <CommandItem onSelect={() => { setActiveFilter("assignee"); setFilterSearch(""); }}>Assignee</CommandItem>
                  <CommandItem onSelect={() => { setActiveFilter("type"); setFilterSearch(""); }}>Type</CommandItem>
                </CommandGroup>
              </CommandList>
            </Command>
          ) : (
            <Command>
              <CommandInput
                placeholder={`Search ${activeFilter}...`}
                value={filterSearch}
                onValueChange={setFilterSearch}
              />
              <CommandList>
                <CommandEmpty>No results found.</CommandEmpty>
                <CommandGroup>
                  {activeFilter === "priority" && filtered(priorities).map((priority) => (
                    <CommandItem key={priority} onSelect={() => onPriorityToggle(priority)}>
                      <div className={cn("mr-2 flex h-4 w-4 items-center justify-center rounded-sm border border-primary", selectedPriorities.includes(priority) ? "bg-primary text-primary-foreground" : "opacity-50 [&_svg]:invisible")}>
                        <CheckIcon className="h-4 w-4" />
                      </div>
                      <span className="capitalize">{priority}</span>
                    </CommandItem>
                  ))}
                  {activeFilter === "status" && filtered(statuses).map((status) => (
                    <CommandItem key={status} onSelect={() => onStatusToggle(status)}>
                      <div className={cn("mr-2 flex h-4 w-4 items-center justify-center rounded-sm border border-primary", selectedStatuses.includes(status) ? "bg-primary text-primary-foreground" : "opacity-50 [&_svg]:invisible")}>
                        <CheckIcon className="h-4 w-4" />
                      </div>
                      <span className="capitalize">{status}</span>
                    </CommandItem>
                  ))}
                  {activeFilter === "assignee" && filtered(assignees).map((assignee) => (
                    <CommandItem key={assignee} onSelect={() => onAssigneeToggle(assignee)}>
                      <div className={cn("mr-2 flex h-4 w-4 items-center justify-center rounded-sm border border-primary", selectedAssignees.includes(assignee) ? "bg-primary text-primary-foreground" : "opacity-50 [&_svg]:invisible")}>
                        <CheckIcon className="h-4 w-4" />
                      </div>
                      {assignee}
                    </CommandItem>
                  ))}
                  {activeFilter === "type" && filtered(TICKET_TYPES).map((type) => (
                    <CommandItem key={type} onSelect={() => onTypeToggle(type)}>
                      <div className={cn("mr-2 flex h-4 w-4 items-center justify-center rounded-sm border border-primary", selectedTypes.includes(type) ? "bg-primary text-primary-foreground" : "opacity-50 [&_svg]:invisible")}>
                        <CheckIcon className="h-4 w-4" />
                      </div>
                      <span className="capitalize">{type}</span>
                    </CommandItem>
                  ))}
                </CommandGroup>
                <CommandSeparator />
                <CommandGroup>
                  <CommandItem onSelect={back} className="justify-center text-center text-muted-foreground">
                    ← Back to filters
                  </CommandItem>
                </CommandGroup>
              </CommandList>
            </Command>
          )}
        </PopoverContent>
      </Popover>

      <div className="flex flex-wrap gap-2">
        {selectedPriorities.map((p) => (
          <FilterBadge key={`p-${p}`} text={`Priority: ${p}`} onRemove={() => onPriorityToggle(p)} />
        ))}
        {selectedStatuses.map((s) => (
          <FilterBadge key={`s-${s}`} text={`Status: ${s}`} onRemove={() => onStatusToggle(s)} />
        ))}
        {selectedAssignees.map((a) => (
          <FilterBadge key={`a-${a}`} text={`Assignee: ${a}`} onRemove={() => onAssigneeToggle(a)} />
        ))}
        {selectedTypes.map((t) => (
          <FilterBadge key={`t-${t}`} text={`Type: ${t}`} onRemove={() => onTypeToggle(t)} />
        ))}
        {hasFilters && (
          <Button variant="ghost" size="sm" className="h-6 px-2 text-xs" onClick={onClearFilters}>
            Clear all
          </Button>
        )}
      </div>
    </div>
  );
}
