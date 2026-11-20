procedure Body;
var D: Variant;
begin
  D := Client.OpenDocument('PCB', '%OUT%\board\board.PcbDoc');
  Client.ShowDocument(D);
  ResetParameters;
  AddStringParameter('Dimension', '3D');
  RunProcess('PCB:SetCurrentView');
  ResetParameters;
  AddStringParameter('Action', 'All');
  RunProcess('PCB:Zoom');
  L('shown');
end;
