import { GlobalConfigForm } from "./GlobalConfigForm"
import { NotifyChannelsEditor } from "./NotifyChannelsEditor"
import { ContactsCard } from "./ContactsCard"

export function SettingsView() {
  return (
    <div className="flex flex-col gap-4">
      <GlobalConfigForm />
      <NotifyChannelsEditor />
      <ContactsCard />
    </div>
  )
}
